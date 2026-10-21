import asyncio
import copy
import io
import json
import sys
import threading
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Helper"))
from ai_filter import AIClassifier, Classification, eligible, request_priority, NoRedirect
from worker import Worker

CONFIG = dict(enabled=True, aiEnabled=True, aiProvider="openrouter", aiModel="openrouter/free",
              aiMinimumPriority="high", aiHourlyLimit=30, keywords=["срочно"], people=[], chats=[],
              aiExcludedChats=[], allowMentions=True, allowReplies=True)


class RequestTests(unittest.TestCase):
    def response(self, content):
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    def test_payload_minimizes_data_and_bounds_input(self):
        opener = SimpleNamespace(open=lambda request, timeout: self.response('{"priority":"high"}'))
        with patch("ai_filter.urllib.request.build_opener", return_value=opener):
            with patch.object(opener, "open", wraps=opener.open) as call:
                self.assertEqual(request_priority("openrouter", "openrouter/free", "TEST_KEY", "x" * 8000, "y" * 3000), "high")
        request = call.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(json.loads(payload["messages"][1]["content"]), {"message": "x" * 4000})
        self.assertTrue(payload["messages"][0]["content"].endswith("y" * 2000))
        self.assertNotIn("TEST_KEY", request.data.decode())
        self.assertEqual(request.get_header("Authorization"), "Bearer TEST_KEY")
        self.assertEqual(payload["provider"]["data_collection"], "deny")
        self.assertEqual(call.call_args.kwargs["timeout"], 8)

    def test_deepseek_uses_fixed_https_endpoint_and_json_mode(self):
        calls = []
        def open_request(request, timeout):
            calls.append(request)
            return self.response('{"priority":"urgent"}')
        with patch("ai_filter.urllib.request.build_opener", return_value=SimpleNamespace(open=open_request)):
            self.assertEqual(request_priority("deepseek", "deepseek-chat", "TEST_KEY", "help", ""), "urgent")
        self.assertEqual(calls[0].full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(json.loads(calls[0].data)["response_format"], {"type": "json_object"})

    def test_response_must_be_a_single_valid_priority(self):
        for content in ['{"priority":"urgent","reason":"private content"}', '{"priority":true}',
                        '{"priority":"HIGH"}', '```json\n{"priority":"high"}\n```', '[]', 'not json']:
            with self.subTest(content=content), patch("ai_filter.urllib.request.build_opener",
                    return_value=SimpleNamespace(open=lambda *a, **kw: self.response(content))):
                with self.assertRaises((ValueError, TypeError)):
                    request_priority("openrouter", "model", "TEST_KEY", "message", "")

    def test_oversized_response_is_rejected(self):
        with patch("ai_filter.urllib.request.build_opener", return_value=SimpleNamespace(
                open=lambda *a, **kw: io.BytesIO(b" " * 16385))):
            with self.assertRaises(ValueError):
                request_priority("openrouter", "model", "TEST_KEY", "message", "")

    def test_redirect_never_reuses_authorization(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))


class ClassifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_is_redacted_and_enters_cooldown(self):
        def fail(*args): raise ValueError("TEST_KEY and PRIVATE_MESSAGE")
        classifier = AIClassifier(request=fail)
        result = await classifier.classify({"text": "PRIVATE_MESSAGE"}, CONFIG, "TEST_KEY")
        self.assertIsNone(result.priority)
        self.assertTrue(result.connection_failed)
        self.assertNotIn("TEST_KEY", result.status)
        self.assertNotIn("PRIVATE_MESSAGE", result.status)
        again = await classifier.classify({"text": "other"}, CONFIG, "TEST_KEY")
        self.assertIn("пауза", again.status)
        self.assertFalse(again.connection_failed)
        self.assertEqual(len(classifier.calls), 1)
        self.assertEqual(classifier.active, 0)

    async def test_hourly_limit_does_not_queue(self):
        classifier = AIClassifier(request=lambda *a: "high")
        config = dict(CONFIG, aiHourlyLimit=1)
        self.assertEqual((await classifier.classify({"text": "a"}, config, "key")).priority, "high")
        self.assertIsNone((await classifier.classify({"text": "b"}, config, "key")).priority)
        self.assertEqual(len(classifier.calls), 1)
        classifier.calls[0] -= 3601
        self.assertEqual((await classifier.classify({"text": "c"}, config, "key")).priority, "high")

    async def test_concurrency_is_bounded_without_waiting_messages(self):
        gate = threading.Event()
        def block(*args):
            gate.wait(timeout=3)
            return "high"
        classifier = AIClassifier(request=block)
        tasks = [asyncio.create_task(classifier.classify({"text": "a"}, CONFIG, "key")) for _ in range(2)]
        try:
            await asyncio.sleep(0.02)
            self.assertEqual(classifier.active, 2)
            self.assertIsNone((await classifier.classify({"text": "third"}, CONFIG, "key")).priority)
            self.assertEqual(len(classifier.calls), 2)
        finally:
            gate.set()
            await asyncio.gather(*tasks)
        self.assertEqual(classifier.active, 0)

    async def test_missing_key_or_invalid_provider_does_not_make_request(self):
        request = unittest.mock.Mock()
        classifier = AIClassifier(request=request)
        for key, config in [("", CONFIG), ("key", dict(CONFIG, aiProvider="https://evil.example")),
                            ("key\nheader", CONFIG), ("key", dict(CONFIG, aiModel=""))]:
            self.assertIsNone((await classifier.classify({"text": "a"}, config, key)).priority)
        request.assert_not_called()

    async def test_timeout_falls_back_but_holds_slot_until_network_thread_ends(self):
        gate = threading.Event()
        def block(*args):
            gate.wait(timeout=2)
            return "high"
        classifier = AIClassifier(request=block, timeout=0.02)
        try:
            result = await classifier.classify({"text": "a"}, CONFIG, "key")
            self.assertIsNone(result.priority)
            self.assertTrue(result.connection_failed)
            self.assertEqual(classifier.active, 1)
        finally:
            gate.set()
            await asyncio.sleep(0.02)
        self.assertEqual(classifier.active, 0)


class WorkerAITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.worker = Worker()
        self.worker.config = copy.deepcopy(CONFIG)
        self.worker.ai_key = "TEST_KEY"
        self.worker.ai.classify = AsyncMock(return_value=Classification("high", "ok"))
        self.worker.is_chat_muted = AsyncMock(return_value=False)
        self.output = io.StringIO()
        self.redirect = redirect_stdout(self.output)
        self.redirect.__enter__()

    def tearDown(self): self.redirect.__exit__(None, None, None)

    def event(self, **changes):
        fields = dict(chat_id=10, id=7, sender_id=10, is_private=True, is_group=False,
            raw_text="Please fix the server today", voice=False, video_note=False, out=False,
            message=SimpleNamespace(action=None), date=datetime.now(timezone.utc),
            get_chat=AsyncMock(return_value=SimpleNamespace(username="example")),
            get_sender=AsyncMock(return_value=SimpleNamespace(first_name="Secret name", last_name=None)))
        fields.update(changes)
        return SimpleNamespace(**fields)

    def messages(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines() if json.loads(line)["type"] == "message"]

    async def test_ai_replaces_keywords_and_respects_priority_threshold(self):
        await self.worker.on_message(self.event())
        self.assertTrue(self.messages()[-1]["allowed"])
        self.worker.ai.classify.return_value = Classification("low", "ok")
        await self.worker.on_message(self.event(id=8, raw_text="срочно buy this ad"))
        self.assertFalse(self.messages()[-1]["allowed"])
        self.assertEqual(self.messages()[-1]["text"], "")
        self.assertNotIn("buy this ad", self.output.getvalue())

    async def test_ai_failure_falls_back_to_local_words(self):
        self.worker.ai.classify.return_value = Classification(None, "fallback")
        await self.worker.on_message(self.event(raw_text="срочно"))
        self.assertTrue(self.messages()[-1]["allowed"])
        await self.worker.on_message(self.event(id=8))
        self.assertFalse(self.messages()[-1]["allowed"])

    async def test_outage_is_distinct_from_limits_and_recovery_in_ipc(self):
        for index, (result, expected) in enumerate([
            (Classification(None, "redacted failure", connection_failed=True), "failed"),
            (Classification(None, "local capacity limit"), "unchanged"),
            (Classification("high", "recovered"), "ready"),
        ]):
            self.worker.ai.classify.return_value = result
            await self.worker.on_message(self.event(id=index))
            statuses = [json.loads(line) for line in self.output.getvalue().splitlines()
                        if json.loads(line)["type"] == "aiStatus"]
            self.assertEqual(statuses[-1]["connection"], expected)

    async def test_excluded_private_chat_never_calls_ai_and_uses_local_rules(self):
        self.worker.config["aiExcludedChats"] = [dict(id="10", name="Private")]
        await self.worker.on_message(self.event())
        self.assertFalse(self.messages()[-1]["allowed"])
        await self.worker.on_message(self.event(id=8, raw_text="срочно"))
        self.assertTrue(self.messages()[-1]["allowed"])
        self.worker.ai.classify.assert_not_awaited()

    async def test_muted_and_unknown_mute_never_call_ai(self):
        for index, muted in enumerate([True, None]):
            self.worker.is_chat_muted.return_value = muted
            await self.worker.on_message(self.event(id=index))
            self.assertFalse(self.messages()[-1]["allowed"])
        self.worker.ai.classify.assert_not_awaited()

    async def test_vip_and_enabled_mention_are_local(self):
        self.worker.config["people"] = [dict(id="10", name="VIP")]
        await self.worker.on_message(self.event())
        await self.worker.on_message(self.event(id=8, is_private=False, is_group=True,
                                               message=SimpleNamespace(action=None, mentioned=True)))
        self.assertTrue(all(x["allowed"] for x in self.messages()))
        self.worker.ai.classify.assert_not_awaited()

    async def test_unlisted_group_is_not_sent_but_selected_keyword_chat_is(self):
        await self.worker.on_message(self.event(is_private=False, is_group=True))
        self.worker.ai.classify.assert_not_awaited()
        self.worker.config["chats"] = [dict(id="10", keywordsOnly=True)]
        await self.worker.on_message(self.event(id=8, is_private=False, is_group=True))
        self.worker.ai.classify.assert_awaited_once()
        self.assertTrue(self.messages()[-1]["allowed"])

    async def test_excluded_group_is_not_sent_even_with_matching_words(self):
        self.worker.config.update(chats=[dict(id="10", keywordsOnly=True)], aiExcludedChats=[dict(id="10")])
        await self.worker.on_message(self.event(is_private=False, is_group=True, raw_text="срочно"))
        self.worker.ai.classify.assert_not_awaited()
        self.assertTrue(self.messages()[-1]["allowed"])

    async def test_paused_filter_does_no_work(self):
        for field in ["enabled", "processingEnabled"]:
            self.worker.config[field] = False
            await self.worker.on_message(object())
            self.worker.config[field] = True
        self.worker.is_chat_muted.assert_not_awaited()
        self.worker.ai.classify.assert_not_awaited()

    async def test_stale_and_service_messages_never_sent(self):
        await self.worker.on_message(self.event(date=datetime.now(timezone.utc) - timedelta(minutes=5)))
        await self.worker.on_message(self.event(id=8, message=SimpleNamespace(action="service")))
        self.worker.ai.classify.assert_not_awaited()

    async def test_adding_exclusion_during_mute_check_prevents_request(self):
        async def exclude(*args):
            await self.worker.command(dict(command="config", config=dict(CONFIG, aiExcludedChats=[dict(id="10")])))
            return False
        self.worker.is_chat_muted.side_effect = exclude
        await self.worker.on_message(self.event())
        self.worker.ai.classify.assert_not_awaited()
        self.assertEqual(self.messages(), [])

    async def test_disable_during_ai_request_discards_result(self):
        async def pause(*args):
            await self.worker.command(dict(command="config", config=dict(CONFIG, enabled=False)))
            return Classification("high", "ok")
        self.worker.ai.classify.side_effect = pause
        await self.worker.on_message(self.event())
        self.assertEqual(self.messages(), [])

    async def test_mute_update_during_ai_request_discards_result(self):
        async def update_mute(*args):
            self.worker.invalidate_notification_settings()
            return Classification("high", "ok")
        self.worker.ai.classify.side_effect = update_mute
        await self.worker.on_message(self.event())
        self.assertEqual(self.messages(), [])

    async def test_disconnect_during_ai_request_discards_result(self):
        async def disconnect(*args):
            await self.worker.close_client()
            return Classification("high", "ok")
        self.worker.ai.classify.side_effect = disconnect
        await self.worker.on_message(self.event())
        self.assertEqual(self.messages(), [])

    async def test_secret_is_not_retained_in_config_or_echoed(self):
        await self.worker.command(dict(command="config", config=dict(CONFIG, aiApiKey="TEST_SECRET")))
        self.assertEqual(self.worker.ai_key, "TEST_SECRET")
        self.assertNotIn("aiApiKey", self.worker.config)
        self.assertNotIn("TEST_SECRET", self.output.getvalue())

    async def test_identical_runtime_update_does_not_invalidate_ai(self):
        await self.worker.command(dict(command="config", config=dict(CONFIG, aiApiKey="TEST_KEY")))
        generation = self.worker.config_generation
        await self.worker.command(dict(command="config", config=dict(CONFIG, aiApiKey="TEST_KEY")))
        self.assertEqual(self.worker.config_generation, generation)


if __name__ == "__main__": unittest.main()
