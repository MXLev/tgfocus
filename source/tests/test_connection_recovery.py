import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Helper'))
from worker import Worker
from telethon import errors

REAL_SLEEP = asyncio.sleep


class Client:
    def __init__(self):
        self.connected = True
        self.ended = asyncio.get_running_loop().create_future()
        self._updates_error = None
        self.failures = []
        self.attempts = 0
        self.me = SimpleNamespace(id=42, first_name='Synthetic', last_name=None)
        self.get_me = AsyncMock(side_effect=lambda: self.me)
        self.session = SimpleNamespace(save=lambda: 'SYNTHETIC_SESSION')
        self.add_event_handler = Mock()
        self.log_out = AsyncMock(return_value=True)

    @property
    def disconnected(self):
        return asyncio.shield(self.ended)

    def is_connected(self):
        return self.connected

    async def connect(self):
        self.attempts += 1
        if self.failures:
            raise self.failures.pop(0)
        self.connected = True
        self._updates_error = None
        if self.ended.done():
            self.ended = asyncio.get_running_loop().create_future()

    async def disconnect(self):
        self.connected = False
        if not self.ended.done():
            self.ended.set_result(None)

    def fail(self, error=None):
        self.connected = False
        if error:
            self.ended.set_exception(error)
        else:
            self.ended.set_result(None)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.worker = Worker()
        self.client = Client()
        self.worker.client = self.client
        self.delays = []
        async def sleep(delay):
            self.delays.append(delay)
            await REAL_SLEEP(0)
        self.sleep_patch = patch('worker.asyncio.sleep', side_effect=sleep)
        self.sleep_patch.start()
        self.output = patch('worker.emit').start()

    async def asyncTearDown(self):
        await self.worker.close_client()
        patch.stopall()

    async def until(self, predicate):
        async def wait():
            while not predicate():
                await REAL_SLEEP(0)
        await asyncio.wait_for(wait(), timeout=2)

    def states(self):
        return [call.kwargs.get('state') for call in self.output.call_args_list]

    async def watch(self):
        self.worker.monitor_task = asyncio.create_task(self.worker.monitor(self.client))
        await REAL_SLEEP(0)

    async def test_terminal_transport_failure_recovers_without_user_action(self):
        self.client.failures = [OSError('offline')] * 6
        await self.watch()
        self.client.fail(ConnectionError('retries exhausted'))
        await self.until(lambda: 'connected' in self.states())
        self.assertEqual(self.client.attempts, 7)
        self.assertTrue(self.client.is_connected())
        self.assertTrue(all(2 <= delay <= 60 for delay in self.delays))
        self.assertEqual(self.delays, sorted(self.delays))
        self.assertIn('reconnecting', self.states())
        self.assertEqual(self.output.call_args.kwargs['state'], 'connected')

    async def test_startup_without_network_retries_with_same_client(self):
        self.worker.client = None
        self.client.failures = [OSError('offline')]
        with patch('telethon.TelegramClient', return_value=self.client):
            await self.worker.configure(dict(apiId='1', apiHash='a' * 32))
        await self.until(lambda: 'connected' in self.states())
        self.assertEqual(self.client.attempts, 2)
        self.assertIs(self.worker.client, self.client)

    async def test_fresh_startup_can_recover_to_login_screen(self):
        self.worker.client = None
        self.client.me = None
        self.client.failures = [OSError('offline')]
        with patch('telethon.TelegramClient', return_value=self.client):
            await self.worker.configure(dict(apiId='1', apiHash='a' * 32))
        await self.until(lambda: 'phone' in self.states())
        self.assertFalse(any(call.args[0] == 'invalidSession' for call in self.output.call_args_list))

    async def test_revoked_session_does_not_retry_forever(self):
        await self.watch()
        self.client.fail(errors.SessionRevokedError(None))
        await self.until(lambda: self.worker.client is None)
        self.assertEqual(self.client.attempts, 0)
        self.assertEqual(self.output.call_args.args, ('invalidSession',))

    async def test_update_loop_revocation_is_not_reported_as_network_retry(self):
        await self.watch()
        self.client._updates_error = errors.AuthKeyUnregisteredError(None)
        self.client.fail()
        await self.until(lambda: self.worker.client is None)
        self.assertEqual(self.output.call_args.args, ('invalidSession',))
        self.assertEqual(self.client.attempts, 0)

    async def test_get_me_revalidates_previously_authorized_session(self):
        await self.watch()
        self.client.me = None
        self.client.fail(ConnectionError())
        await self.until(lambda: self.worker.client is None)
        self.assertEqual(self.output.call_args.args, ('invalidSession',))

    async def test_server_flood_wait_is_honored(self):
        await self.watch()
        self.client.failures = [errors.FloodWaitError(None, capture=120)]
        self.client.fail(ConnectionError())
        await self.until(lambda: 'connected' in self.states())
        self.assertIn(120, self.delays)

    async def test_manual_replacement_cancels_retry_of_old_account(self):
        sleeping = asyncio.Event()
        async def block(_delay):
            sleeping.set()
            await asyncio.Event().wait()
        self.sleep_patch.stop()
        with patch('worker.asyncio.sleep', side_effect=block):
            await self.watch()
            self.client.fail(ConnectionError())
            await asyncio.wait_for(sleeping.wait(), timeout=1)
            other = Client()
            with patch('telethon.TelegramClient', return_value=other):
                await self.worker.configure(dict(apiId='2', apiHash='b' * 32))
        self.assertEqual(self.client.attempts, 0)
        self.assertIs(self.worker.client, other)
        self.assertEqual(other.attempts, 1)

    async def test_manual_replacement_cancels_old_invalid_session_cleanup(self):
        cleaning = asyncio.Event()
        disconnect = self.client.disconnect
        calls = 0
        async def delayed_disconnect():
            nonlocal calls
            calls += 1
            if calls == 1:
                cleaning.set()
                await asyncio.Event().wait()
            await disconnect()
        self.client.disconnect = delayed_disconnect
        await self.watch()
        self.client.fail(errors.SessionRevokedError(None))
        await asyncio.wait_for(cleaning.wait(), timeout=1)
        other = Client()
        with patch('telethon.TelegramClient', return_value=other):
            await self.worker.configure(dict(apiId='2', apiHash='b' * 32))
        self.assertIs(self.worker.client, other)
        self.assertEqual(self.worker.current_user_id, other.me.id)
        self.assertFalse(any(call.args[0] == 'invalidSession' for call in self.output.call_args_list))
        self.assertEqual(self.output.call_args.kwargs['state'], 'connected')

    async def test_logout_cancels_observer_without_disconnecting_before_rpc(self):
        await self.watch()
        async def logout():
            self.assertTrue(self.client.is_connected())
            return True
        self.client.log_out.side_effect = logout
        await self.worker.command(dict(command='logout'))
        self.client.log_out.assert_awaited_once()
        self.assertIsNone(self.worker.client)
        self.assertIsNone(self.worker.monitor_task)
        self.assertEqual(self.output.call_args.args, ('loggedOut',))

    async def test_failed_logout_resumes_observer(self):
        await self.watch()
        self.client.log_out.return_value = False
        await self.worker.command(dict(command='logout'))
        self.assertIsNotNone(self.worker.monitor_task)
        self.client.fail(ConnectionError())
        await self.until(lambda: 'connected' in self.states())
        self.assertEqual(self.client.attempts, 1)


if __name__ == '__main__':
    unittest.main()
