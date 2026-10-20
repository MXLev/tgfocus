from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Helper'))
from rules import decide
from worker import Worker
from telethon.tl import types


class MutedRulesTests(unittest.TestCase):
    def test_muting_overrides_all_rule_exceptions(self):
        config = dict(enabled=True, allPrivate=True, keywords=['срочно'],
            allowMentions=True, allowReplies=True, people=[dict(id='42')],
            chats=[dict(id='10', keywordsOnly=False)])
        for kind in ['private', 'group', 'channel']:
            for state in ['muted', 'muteUnknown']:
                with self.subTest(kind=kind, state=state):
                    result = decide(dict(kind=kind, chatId='10', senderId='42', text='срочно',
                        mentioned=True, replyToMe=True, **{state: True}), config)
                    self.assertFalse(result.allowed)


class NotificationSettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.worker = Worker()
        self.worker.client = AsyncMock()
        self.event = SimpleNamespace(chat_id=-10, get_input_chat=AsyncMock(return_value=types.InputPeerChat(10)))
        self.future = datetime.now(timezone.utc) + timedelta(days=1)
        self.epoch = datetime.fromtimestamp(0, timezone.utc)

    async def test_per_chat_mute_is_respected(self):
        self.worker.client.return_value = types.PeerNotifySettings(mute_until=self.future)
        self.assertTrue(await self.worker.is_chat_muted(self.event, 'group'))
        self.assertIsInstance(self.worker.client.call_args.args[0].peer, types.InputNotifyPeer)
        self.worker.client.assert_awaited_once()

    async def test_explicit_unmute_does_not_inherit_global_mute(self):
        self.worker.client.return_value = types.PeerNotifySettings(mute_until=self.epoch)
        self.assertFalse(await self.worker.is_chat_muted(self.event, 'group'))
        self.worker.client.assert_awaited_once()

    async def test_correct_global_category_is_inherited(self):
        for kind, category in [('private', types.InputNotifyUsers), ('group', types.InputNotifyChats),
                               ('channel', types.InputNotifyBroadcasts)]:
            self.worker.invalidate_notification_settings()
            self.worker.client.side_effect = [types.PeerNotifySettings(), types.PeerNotifySettings(mute_until=self.future)]
            with self.subTest(kind=kind):
                self.assertTrue(await self.worker.is_chat_muted(self.event, kind))
                self.assertIsInstance(self.worker.client.call_args.args[0].peer, category)

    async def test_unset_global_mute_means_enabled(self):
        self.worker.client.return_value = types.PeerNotifySettings()
        self.assertFalse(await self.worker.is_chat_muted(self.event, 'group'))

    async def test_expired_temporary_mute_means_enabled(self):
        self.worker.client.return_value = types.PeerNotifySettings(mute_until=datetime.now(timezone.utc) - timedelta(seconds=1))
        self.assertFalse(await self.worker.is_chat_muted(self.event, 'group'))

    async def test_cached_temporary_mute_expires_without_new_request(self):
        settings = types.PeerNotifySettings(mute_until=self.future)
        self.worker.client.return_value = settings
        self.assertTrue(await self.worker.is_chat_muted(self.event, 'group'))
        settings.mute_until = self.epoch
        self.assertFalse(await self.worker.is_chat_muted(self.event, 'group'))
        self.worker.client.assert_awaited_once()

    async def test_live_update_invalidates_cached_unmute(self):
        self.worker.client.return_value = types.PeerNotifySettings(mute_until=self.epoch)
        self.assertFalse(await self.worker.is_chat_muted(self.event, 'group'))
        muted = types.PeerNotifySettings(mute_until=self.future)
        await self.worker.on_notify_settings(types.UpdateNotifySettings(types.NotifyPeer(types.PeerChat(10)), muted))
        self.worker.client.return_value = muted
        self.assertTrue(await self.worker.is_chat_muted(self.event, 'group'))
        self.assertEqual(self.worker.client.await_count, 2)

    async def test_expired_cache_refetches_settings(self):
        self.worker.notify_cache[('peer', -10)] = (0, types.PeerNotifySettings(mute_until=self.epoch))
        self.worker.client.return_value = types.PeerNotifySettings(mute_until=self.future)
        self.assertTrue(await self.worker.is_chat_muted(self.event, 'group'))
        self.worker.client.assert_awaited_once()

    async def test_request_failure_is_unknown_and_backed_off(self):
        self.worker.client.side_effect = RuntimeError('offline')
        self.assertIsNone(await self.worker.is_chat_muted(self.event, 'group'))
        self.assertIsNone(await self.worker.is_chat_muted(self.event, 'group'))
        self.worker.client.assert_awaited_once()

    async def test_missing_global_settings_is_unknown(self):
        self.worker.client.side_effect = [types.PeerNotifySettings(), RuntimeError('offline')]
        self.assertIsNone(await self.worker.is_chat_muted(self.event, 'group'))

    async def test_update_during_fetch_does_not_cache_stale_response(self):
        async def fetch(request):
            self.worker.invalidate_notification_settings()
            return types.PeerNotifySettings(mute_until=self.epoch)
        self.worker.client.side_effect = fetch
        self.assertIsNone(await self.worker.is_chat_muted(self.event, 'group'))
        self.assertFalse(self.worker.notify_cache)

    def forum_message(self, top_id=30, reply_id=31):
        self.event.message = SimpleNamespace(reply_to=types.MessageReplyHeader(
            forum_topic=True, reply_to_top_id=top_id, reply_to_msg_id=reply_id))
        return self.event

    async def test_muted_forum_topic_is_checked_before_parent(self):
        self.worker.client.return_value = types.PeerNotifySettings(mute_until=self.future)
        self.assertTrue(await self.worker.is_chat_muted(self.forum_message(), 'group'))
        request = self.worker.client.call_args.args[0].peer
        self.assertIsInstance(request, types.InputNotifyForumTopic)
        self.assertEqual(request.top_msg_id, 30)
        self.worker.client.assert_awaited_once()

    async def test_topic_without_top_id_uses_replied_root_message(self):
        self.worker.client.return_value = types.PeerNotifySettings(mute_until=self.future)
        self.assertTrue(await self.worker.is_chat_muted(self.forum_message(top_id=None, reply_id=30), 'group'))
        self.assertEqual(self.worker.client.call_args.args[0].peer.top_msg_id, 30)

    async def test_explicit_topic_unmute_overrides_parent(self):
        self.worker.client.side_effect = [types.PeerNotifySettings(mute_until=self.epoch),
            types.PeerNotifySettings(mute_until=self.future)]
        self.assertFalse(await self.worker.is_chat_muted(self.forum_message(), 'group'))
        self.worker.client.assert_awaited_once()

    async def test_unset_topic_mute_inherits_parent(self):
        self.worker.client.side_effect = [types.PeerNotifySettings(), types.PeerNotifySettings(mute_until=self.future)]
        self.assertTrue(await self.worker.is_chat_muted(self.forum_message(), 'group'))
        self.assertIsInstance(self.worker.client.call_args.args[0].peer, types.InputNotifyPeer)

    async def test_topic_and_parent_can_inherit_global_group_mute(self):
        self.worker.client.side_effect = [types.PeerNotifySettings(), types.PeerNotifySettings(),
            types.PeerNotifySettings(mute_until=self.future)]
        self.assertTrue(await self.worker.is_chat_muted(self.forum_message(), 'group'))
        self.assertIsInstance(self.worker.client.call_args.args[0].peer, types.InputNotifyChats)

    async def test_general_forum_topic_uses_id_one(self):
        self.event.chat = SimpleNamespace(forum=True)
        self.worker.client.return_value = types.PeerNotifySettings(mute_until=self.future)
        self.assertTrue(await self.worker.is_chat_muted(self.event, 'group'))
        self.assertEqual(self.worker.client.call_args.args[0].peer.top_msg_id, 1)

    async def test_unavailable_topic_settings_do_not_fall_back_to_unmuted_parent(self):
        self.worker.client.side_effect = RuntimeError('unavailable')
        self.assertIsNone(await self.worker.is_chat_muted(self.forum_message(), 'group'))
        self.worker.client.assert_awaited_once()

    async def test_topic_cache_does_not_leak_between_topics(self):
        self.worker.client.side_effect = [types.PeerNotifySettings(mute_until=self.future),
            types.PeerNotifySettings(mute_until=self.epoch)]
        self.assertTrue(await self.worker.is_chat_muted(self.forum_message(top_id=30), 'group'))
        self.assertFalse(await self.worker.is_chat_muted(self.forum_message(top_id=40), 'group'))
        self.assertEqual(self.worker.client.await_count, 2)

    async def test_non_forum_reply_uses_parent_chat_settings(self):
        self.event.message = SimpleNamespace(reply_to=types.MessageReplyHeader(reply_to_msg_id=30))
        self.worker.client.return_value = types.PeerNotifySettings(mute_until=self.epoch)
        self.assertFalse(await self.worker.is_chat_muted(self.event, 'group'))
        self.assertIsInstance(self.worker.client.call_args.args[0].peer, types.InputNotifyPeer)
