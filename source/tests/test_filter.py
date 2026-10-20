import asyncio
import copy
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Helper"))
from rules import decide, keyword_match
from worker import Worker

BASE = dict(enabled=True, allPrivate=False, keywords=["срочно", "позвони", "нужна помощь"],
            people=[dict(id="42", name="Важный человек")],
            chats=[dict(id="-100123", name="Учёба", keywordsOnly=True),
                   dict(id="-100456", name="Всегда", keywordsOnly=False)])


class RulesTests(unittest.TestCase):
    def check(self, allowed, text="Привет", **fields):
        message = dict(kind="private", chatId="10", senderId="10", text=text)
        message.update(fields)
        self.assertEqual(decide(message, BASE).allowed, allowed)

    def test_private_urgent(self): self.check(True, "Можешь СРОЧНО позвонить?")
    def test_private_casual(self): self.check(False)
    def test_vip_no_text(self): self.check(True, "", senderId="42")
    def test_vip_does_not_override_group(self): self.check(False, "срочно", kind="group", senderId="42")
    def test_group_urgent_still_muted(self): self.check(False, "срочно", kind="group")
    def test_channel_urgent_still_muted(self): self.check(False, "срочно", kind="channel")
    def test_chat_exception_keyword(self): self.check(True, "срочно", kind="group", chatId="-100123")
    def test_chat_exception_requires_keyword(self): self.check(False, "Привет", kind="group", chatId="-100123")
    def test_chat_exception_all(self): self.check(True, "Привет", kind="channel", chatId="-100456")
    def test_group_personal_mention_passes(self): self.check(True, "Привет", kind="group", mentioned=True)
    def test_mention_switch_off(self):
        self.assertFalse(decide(dict(kind="group", mentioned=True, text="Привет"), dict(BASE, allowMentions=False)).allowed)

    def test_mentions_and_replies_can_be_switched_independently(self):
        for mentions in (False, True):
            for replies in (False, True):
                config = dict(BASE, allowMentions=mentions, allowReplies=replies)
                for mentioned, replied in ((True, False), (False, True), (True, True)):
                    with self.subTest(mentions=mentions, replies=replies, mentioned=mentioned, replied=replied):
                        result = decide(dict(kind="group", mentioned=mentioned, replyToMe=replied), config)
                        self.assertEqual(result.allowed, (mentions and mentioned) or (replies and replied))

    def test_legacy_combined_switch_is_preserved_for_replies(self):
        for enabled in (True, False):
            self.assertEqual(decide(dict(kind="group", replyToMe=True), dict(BASE, allowMentions=enabled)).allowed, enabled)

    def test_reply_does_not_override_pause_stale_outgoing_or_channel(self):
        for override in [dict(stale=True), dict(outgoing=True), dict(service=True), dict(kind="channel")]:
            message = dict(kind="group", replyToMe=True)
            message.update(override)
            self.assertFalse(decide(message, dict(BASE, allowReplies=True)).allowed)
        self.assertFalse(decide(dict(kind="group", replyToMe=True), dict(BASE, enabled=False)).allowed)
    def test_mention_does_not_override_pause(self):
        self.assertFalse(decide(dict(kind="group", mentioned=True), dict(BASE, enabled=False)).allowed)
    def test_mention_does_not_override_stale(self): self.check(False, kind="group", mentioned=True, stale=True)
    def test_someone_elses_mention_does_not_pass(self): self.check(False, "@someone_else привет", kind="group", mentioned=False)
    def test_channel_mention_not_group_exception(self): self.check(False, kind="channel", mentioned=True)
    def test_mention_overrides_chat_keyword_requirement(self): self.check(True, kind="group", chatId="-100123", mentioned=True)
    def test_outgoing_never_notifies(self): self.check(False, "срочно", outgoing=True, senderId="42")
    def test_service_never_notifies(self): self.check(False, "срочно", service=True, senderId="42")
    def test_stale_never_notifies(self): self.check(False, "срочно", stale=True, senderId="42")
    def test_pause_precedes_vip(self):
        config = dict(BASE, enabled=False)
        self.assertFalse(decide(dict(kind="private", senderId="42", text="срочно"), config).allowed)
    def test_all_private_only_private(self):
        config = dict(BASE, allPrivate=True)
        self.assertTrue(decide(dict(kind="private", text="Привет"), config).allowed)
        self.assertFalse(decide(dict(kind="group", text="Привет"), config).allowed)
    def test_word_boundary(self):
        for text in ["несрочно", "срочность", "срочного", "суперсрочно", "срочно123"]:
            with self.subTest(text=text): self.check(False, text)
    def test_punctuation(self): self.check(True, "«Срочно!»")
    def test_phrase_whitespace(self): self.check(True, "НУЖНА\n  помощь")
    def test_yo_and_case(self): self.assertEqual(keyword_match("ВСЁ готово", ["все готово"]), "все готово")
    def test_regex_metacharacters_are_literal(self):
        self.assertIsNone(keyword_match("любой текст", [".*"]))
        self.assertEqual(keyword_match("знак .* тут", [".*"]), ".*")
    def test_empty_keywords_no_match(self): self.assertIsNone(keyword_match("", ["", " "]))
    def test_numerical_ids_normalized(self):
        self.assertTrue(decide(dict(kind="private", senderId=42), BASE).allowed)


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.worker = Worker()
        self.worker.config = copy.deepcopy(BASE)
        # Event tests isolate rule/auth handling; real mute-policy requests have
        # their own tests below.
        muting = patch.object(self.worker, 'is_chat_muted', new=AsyncMock(return_value=False))
        muting.start()
        self.addCleanup(muting.stop)
        self.output = io.StringIO()
        self.redirect = redirect_stdout(self.output)
        self.redirect.__enter__()

    def tearDown(self): self.redirect.__exit__(None, None, None)
    def messages(self): return [json.loads(line) for line in self.output.getvalue().splitlines()]

    async def test_ipc_simulation_and_pause(self):
        await self.worker.command(dict(command="simulate", message=dict(kind="private", text="срочно")))
        self.assertTrue(self.messages()[-1]["allowed"])
        await self.worker.command(dict(command="config", config=dict(BASE, enabled=False)))
        await self.worker.command(dict(command="simulate", message=dict(kind="private", text="срочно")))
        self.assertFalse(self.messages()[-1]["allowed"])

    def event(self, **changes):
        fields = dict(chat_id=10, id=7, sender_id=10, is_private=True, is_group=False,
                      raw_text="срочно", voice=False, video_note=False, out=False,
                      message=SimpleNamespace(action=None), date=datetime.now(timezone.utc),
                      get_chat=AsyncMock(return_value=SimpleNamespace(username="example")),
                      get_sender=AsyncMock(return_value=SimpleNamespace(first_name="Иван", last_name=None)))
        fields.update(changes)
        return SimpleNamespace(**fields)

    async def test_disabled_filter_skips_all_event_work(self):
        for config in [dict(BASE, enabled=False), dict(BASE, processingEnabled=False)]:
            self.worker.config = config
            await self.worker.on_message(object())  # Even reading event fields would fail.
        self.worker.is_chat_muted.assert_not_awaited()
        self.assertEqual(self.messages(), [])
        self.assertEqual(len(self.worker.seen), 0)

    async def test_pause_during_mute_request_does_not_emit_message(self):
        async def pause(*args):
            self.worker.config["processingEnabled"] = False
            return False
        self.worker.is_chat_muted.side_effect = pause
        await self.worker.on_message(self.event())
        self.assertEqual(self.messages(), [])

    async def test_resuming_runtime_processing_uses_current_rules(self):
        self.worker.config["processingEnabled"] = False
        await self.worker.on_message(self.event())
        self.worker.config["processingEnabled"] = True
        await self.worker.on_message(self.event())
        self.assertEqual(len(self.messages()), 1)
        self.assertTrue(self.messages()[0]["allowed"])

    async def test_message_event_and_duplicate(self):
        event = self.event()
        await self.worker.on_message(event)
        await self.worker.on_message(event)
        self.assertEqual(len(self.messages()), 1)
        item = self.messages()[0]
        self.assertTrue(item["allowed"])
        self.assertEqual(item["text"], "срочно")
        self.assertEqual(item["url"], "tg://resolve?domain=example")

    async def test_suppressed_text_not_forwarded(self):
        await self.worker.on_message(self.event(raw_text="секретный обычный текст"))
        self.assertFalse(self.messages()[0]["allowed"])
        self.assertNotIn("секретный обычный текст", self.output.getvalue())

    async def test_muted_chat_suppresses_matching_keyword_without_forwarding_text(self):
        self.worker.is_chat_muted.return_value = True
        await self.worker.on_message(self.event(raw_text="срочно приватное"))
        self.assertFalse(self.messages()[-1]["allowed"])
        self.assertEqual(self.messages()[-1]["text"], "")
        self.assertIn("выключены в Telegram", self.messages()[-1]["reason"])

    async def test_unknown_mute_settings_suppress_notification(self):
        self.worker.is_chat_muted.return_value = None
        await self.worker.on_message(self.event())
        self.assertFalse(self.messages()[-1]["allowed"])
        self.assertIn("Не удалось проверить", self.messages()[-1]["reason"])

    async def test_rules_rejection_does_not_fetch_notification_settings(self):
        await self.worker.on_message(self.event(raw_text="обычный текст"))
        self.worker.is_chat_muted.assert_not_awaited()

    async def test_telegram_mention_flag_reaches_rules(self):
        await self.worker.on_message(self.event(is_private=False, is_group=True, raw_text="Привет",
            message=SimpleNamespace(action=None, mentioned=True)))
        self.assertTrue(self.messages()[0]["allowed"])
        self.assertEqual(self.messages()[0]["reason"], "Упоминание тебя")

    def reply_event(self, *, original_out=True, entities=(), fetch_error=None):
        self.worker.current_user_id = 42
        self.worker.current_usernames = {"myname"}
        return self.event(is_private=False, is_group=True, raw_text="Привет", is_reply=True,
            message=SimpleNamespace(action=None, mentioned=True, get_entities_text=lambda: entities),
            get_reply_message=AsyncMock(return_value=SimpleNamespace(out=original_out), side_effect=fetch_error))

    async def test_reply_to_me_uses_reply_switch_instead_of_telegram_mention_flag(self):
        self.worker.config.update(allowMentions=True, allowReplies=False)
        await self.worker.on_message(self.reply_event())
        self.assertFalse(self.messages()[-1]["allowed"])

    async def test_reply_to_me_passes_with_mentions_disabled(self):
        self.worker.config.update(allowMentions=False, allowReplies=True)
        await self.worker.on_message(self.reply_event())
        self.assertTrue(self.messages()[-1]["allowed"])
        self.assertEqual(self.messages()[-1]["reason"], "Ответ на твоё сообщение")

    async def test_explicit_username_mention_in_reply_can_pass_independently(self):
        from telethon.tl.types import MessageEntityMention
        self.worker.config.update(allowMentions=True, allowReplies=False)
        await self.worker.on_message(self.reply_event(entities=[(MessageEntityMention(0, 7), "@MyName")]))
        self.assertTrue(self.messages()[-1]["allowed"])
        self.assertEqual(self.messages()[-1]["reason"], "Упоминание тебя")

    async def test_mention_of_another_user_does_not_override_disabled_replies(self):
        from telethon.tl.types import MessageEntityMention
        self.worker.config.update(allowMentions=True, allowReplies=False)
        await self.worker.on_message(self.reply_event(entities=[(MessageEntityMention(0, 6), "@other")]))
        self.assertFalse(self.messages()[-1]["allowed"])

    async def test_reply_to_someone_else_is_not_reply_to_me(self):
        self.worker.config.update(allowMentions=False, allowReplies=True)
        await self.worker.on_message(self.reply_event(original_out=False))
        self.assertFalse(self.messages()[-1]["allowed"])

    async def test_unavailable_reply_does_not_turn_into_mention(self):
        self.worker.config.update(allowMentions=True, allowReplies=False)
        await self.worker.on_message(self.reply_event(fetch_error=RuntimeError('unavailable')))
        self.assertFalse(self.messages()[-1]["allowed"])

    async def test_id_mention_in_unavailable_reply_still_passes(self):
        from telethon.tl.types import MessageEntityMentionName
        self.worker.config.update(allowMentions=True, allowReplies=False)
        await self.worker.on_message(self.reply_event(fetch_error=RuntimeError('unavailable'),
            entities=[(MessageEntityMentionName(0, 4, 42), "User")]))
        self.assertTrue(self.messages()[-1]["allowed"])

    async def test_old_backlog_suppressed(self):
        await self.worker.on_message(self.event(date=datetime.now(timezone.utc) - timedelta(minutes=10)))
        self.assertFalse(self.messages()[0]["allowed"])

    async def test_vip_voice(self):
        await self.worker.on_message(self.event(sender_id=42, raw_text="", voice=True))
        self.assertTrue(self.messages()[0]["allowed"])
        self.assertEqual(self.messages()[0]["text"], "Голосовое сообщение")

    async def test_private_channel_link(self):
        await self.worker.on_message(self.event(chat_id=-100456, is_private=False, is_group=False,
            get_chat=AsyncMock(return_value=SimpleNamespace(username=None, title="Канал"))))
        self.assertEqual(self.messages()[0]["url"], "tg://privatepost?channel=456&post=7")

    async def test_phone_link(self):
        await self.worker.on_message(self.event(get_chat=AsyncMock(return_value=SimpleNamespace(username=None, phone="79990000000"))))
        self.assertEqual(self.messages()[0]["url"], "tg://resolve?phone=79990000000")

    async def test_no_public_link_opens_telegram(self):
        await self.worker.on_message(self.event(get_chat=AsyncMock(return_value=SimpleNamespace(username=None))))
        self.assertEqual(self.messages()[0]["url"], "tg://")

    async def test_entity_lookup_failure_does_not_drop_urgent_message(self):
        await self.worker.on_message(self.event(get_chat=AsyncMock(side_effect=OSError()), get_sender=AsyncMock(side_effect=OSError())))
        self.assertTrue(self.messages()[0]["allowed"])

    async def test_auth_code_needs_password_then_session(self):
        from telethon.errors import SessionPasswordNeededError
        client = SimpleNamespace(
            sign_in=AsyncMock(side_effect=[SessionPasswordNeededError(None), None]),
            session=SimpleNamespace(save=lambda: "FAKE_SESSION_FOR_TEST"),
            get_me=AsyncMock(return_value=SimpleNamespace(first_name="Тест", last_name=None)))
        self.worker.client = client
        await self.worker.command(dict(command="code", code="12345"))
        self.assertEqual(self.messages()[-1]["state"], "password")
        await self.worker.command(dict(command="password", password="FAKE_PASSWORD"))
        self.assertEqual(self.messages()[-1]["state"], "connected")
        self.assertTrue(any(x["type"] == "session" for x in self.messages()))
        self.assertNotIn("FAKE_PASSWORD", self.output.getvalue())
        self.assertNotIn("12345", self.output.getvalue())
        self.worker.monitor_task.cancel()

    async def test_invalid_code_recoverable(self):
        from telethon.errors import PhoneCodeInvalidError
        self.worker.client = SimpleNamespace(sign_in=AsyncMock(side_effect=PhoneCodeInvalidError(None)))
        await self.worker.command(dict(command="code", code="00000"))
        self.assertTrue(self.messages()[-1]["recoverable"])

    async def test_logout_revokes_and_disconnects(self):
        client = SimpleNamespace(log_out=AsyncMock(), disconnect=AsyncMock())
        self.worker.client = client
        await self.worker.command(dict(command="logout"))
        client.log_out.assert_awaited_once()
        client.disconnect.assert_awaited_once()
        self.assertEqual(self.messages()[-1]["type"], "loggedOut")

    async def test_runtime_exception_does_not_leak_secret(self):
        self.worker.client = SimpleNamespace(sign_in=AsyncMock(side_effect=ValueError("SECRET_PASSWORD")))
        await self.worker.command(dict(command="password", password="SECRET_PASSWORD"))
        self.assertNotIn("SECRET_PASSWORD", self.output.getvalue())
        self.assertIn("ValueError", self.messages()[-1]["message"])

    async def test_actual_telegram_delivery_is_reported(self):
        from telethon.tl import types
        sent = types.auth.SentCode(types.auth.SentCodeTypeApp(5), "FAKE_HASH", types.auth.CodeTypeSms(), 60)
        self.worker.client = SimpleNamespace(send_code_request=AsyncMock(return_value=sent))
        await self.worker.command(dict(command="phone", phone="+7 (999) 000-00-00"))
        self.worker.client.send_code_request.assert_awaited_once_with("79990000000")
        delivery, state = self.messages()
        self.assertEqual(delivery["codeType"], "SentCodeTypeApp")
        self.assertTrue(delivery["canResend"])
        self.assertEqual(state["state"], "code")
        self.assertIn("служебный чат", state["message"])
        self.assertNotIn("FAKE_HASH", self.output.getvalue())

    async def test_sms_does_not_claim_telegram_delivery(self):
        from telethon.tl import types
        self.worker.code_sent(types.auth.SentCode(types.auth.SentCodeTypeSms(5), "FAKE_HASH"))
        self.assertIn("SMS", self.messages()[-1]["message"])
        self.assertNotIn("служебный чат", self.messages()[-1]["message"])
        self.assertFalse(self.messages()[0]["canResend"])

    async def test_unsupported_email_suggests_qr(self):
        from telethon.tl import types
        self.worker.code_sent(types.auth.SentCode(types.auth.SentCodeTypeEmailCode("t***@example.org", 6), "FAKE_HASH"))
        self.assertEqual(self.messages()[-1]["state"], "authUnsupported")
        self.assertIn("QR", self.messages()[-1]["message"])
        self.assertNotIn("t***@example.org", self.output.getvalue())

    async def test_resend_timer_blocks_request(self):
        from telethon.tl import types
        self.worker.client = AsyncMock()
        self.worker.code_sent(types.auth.SentCode(types.auth.SentCodeTypeApp(5), "FAKE_HASH", types.auth.CodeTypeSms(), 90))
        await self.worker.command(dict(command="resend"))
        self.worker.client.assert_not_awaited()
        self.assertEqual(self.messages()[-1]["type"], "error")

    async def test_resend_uses_telegram_next_method(self):
        from telethon.tl import types
        self.worker.phone = "79990000000"
        self.worker.code_sent(types.auth.SentCode(types.auth.SentCodeTypeApp(5), "FAKE_HASH", types.auth.CodeTypeSms(), 0))
        self.worker.client = AsyncMock(return_value=types.auth.SentCode(types.auth.SentCodeTypeSms(5), "NEW_FAKE_HASH"))
        await self.worker.command(dict(command="resend"))
        self.worker.client.assert_awaited_once()
        self.assertEqual(self.worker.phone_hash, "NEW_FAKE_HASH")
        self.assertEqual(self.messages()[-2]["method"], "SMS")

    async def test_flood_wait_is_observed_locally(self):
        from telethon.errors import FloodWaitError
        self.worker.client = SimpleNamespace(send_code_request=AsyncMock(side_effect=FloodWaitError(None, capture=60)))
        await self.worker.command(dict(command="phone", phone="+79990000000"))
        await self.worker.command(dict(command="phone", phone="+79990000000"))
        self.worker.client.send_code_request.assert_awaited_once()
        self.assertIn("Осталось", self.messages()[-1]["message"])

    async def setup_qr(self, outcome="success"):
        from telethon.errors import SessionPasswordNeededError
        self.scan = asyncio.Event()
        self.listening = False
        async def wait():
            self.listening = True
            await self.scan.wait()
            if outcome == "password": raise SessionPasswordNeededError(None)
            if outcome == "expired": raise asyncio.TimeoutError()
        qr = SimpleNamespace(url="tg://login?token=TEST_TOKEN", expires=datetime.now(timezone.utc) + timedelta(seconds=30), wait=wait)
        self.worker.client = SimpleNamespace(qr_login=AsyncMock(return_value=qr),
            get_me=AsyncMock(return_value=SimpleNamespace(first_name="Тест", last_name=None)),
            session=SimpleNamespace(save=lambda: "FAKE_SESSION"))
        await self.worker.command(dict(command="qr"))

    async def test_qr_listener_starts_before_display(self):
        await self.setup_qr()
        self.assertTrue(self.listening)
        self.assertEqual(self.messages()[-1]["type"], "qr")
        self.assertFalse(self.worker.qr_task.done())
        await self.worker.command(dict(command="cancelAuth"))
        self.assertIsNone(self.worker.qr_task)
        self.assertEqual(self.messages()[-1]["state"], "phone")

    async def test_qr_success_saves_session(self):
        await self.setup_qr()
        self.scan.set()
        await self.worker.qr_task
        self.assertEqual(self.messages()[-1]["state"], "connected")
        self.assertEqual(self.messages()[-2]["type"], "session")
        self.worker.monitor_task.cancel()

    async def test_qr_two_factor_password(self):
        await self.setup_qr("password")
        self.scan.set()
        await self.worker.qr_task
        self.assertEqual(self.messages()[-1]["state"], "password")

    async def test_qr_expiry_does_not_send_new_requests(self):
        await self.setup_qr("expired")
        self.scan.set()
        await self.worker.qr_task
        self.assertEqual(self.messages()[-1]["state"], "qrExpired")
        self.worker.client.qr_login.assert_awaited_once()


if __name__ == "__main__": unittest.main()
