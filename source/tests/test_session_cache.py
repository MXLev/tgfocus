import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Helper'))
from session_cache import BoundedStringSession
from telethon import TelegramClient
from telethon.crypto import AuthKey
from telethon.sessions import StringSession
from telethon.tl import types


class SessionTests(unittest.TestCase):
    def test_auth_string_roundtrip_does_not_include_cached_people(self):
        session = BoundedStringSession()
        session.set_dc(2, '149.154.167.51', 443)
        session.auth_key = AuthKey(b'a' * 256)
        session.process_entities([types.User(42, access_hash=99, first_name='Private name')])
        restored = StringSession(session.save())
        self.assertEqual(restored.auth_key.key, session.auth_key.key)
        self.assertEqual(restored.dc_id, 2)
        self.assertEqual(BoundedStringSession(session.save()).save(), session.save())
        with self.assertRaises(ValueError):
            restored.get_input_entity(42)

    def test_updates_replace_old_identity_and_preserve_public_lookups(self):
        session = BoundedStringSession(entity_limit=2)
        session.process_entities([types.User(42, access_hash=11, first_name='Old', username='oldname', phone='12345')])
        session.process_entities([types.User(42, access_hash=22, first_name='New', username='newname', phone='67890')])
        for key in ('newname', '67890', 'New', 42, types.PeerUser(42)):
            self.assertEqual(session.get_input_entity(key).access_hash, 22)
        for key in ('oldname', '12345', 'Old'):
            with self.assertRaises(ValueError):
                session.get_input_entity(key)
        self.assertEqual(len(session._entity_rows), 1)

    def test_eviction_preserves_recently_used_peer_and_new_channel(self):
        session = BoundedStringSession(entity_limit=2)
        session.process_entities([types.InputPeerUser(42, 11), types.InputPeerChat(10)])
        self.assertEqual(session.get_input_entity(42).access_hash, 11)
        session.process_entities([types.InputPeerChannel(20, 22)])
        self.assertEqual(session.get_input_entity(-1000000000020).access_hash, 22)
        self.assertEqual(session.get_input_entity(42).access_hash, 11)
        with self.assertRaises(ValueError):
            session.get_input_entity(-10)
        self.assertEqual(len(session._entity_rows), 2)


class IncomingEntityTests(unittest.IsolatedAsyncioTestCase):
    async def test_telethon_preprocessing_is_bounded_before_message_filter_runs(self):
        session = BoundedStringSession(entity_limit=100)
        client = TelegramClient(session, 1, 'a' * 32)
        for batch in range(10):
            users = [types.User(i, access_hash=i, first_name='Synthetic')
                     for i in range(batch * 100 + 1, batch * 100 + 101)]
            await client._preprocess_updates([], users, [])
            self.assertLessEqual(len(session._entity_rows), 100)
        self.assertEqual(session.get_input_entity(1000).user_id, 1000)
        with self.assertRaises(ValueError):
            session.get_input_entity(1)
        self.assertEqual(len(session._entities), 0)  # No second, unbounded MemorySession cache.


if __name__ == '__main__':
    unittest.main()
