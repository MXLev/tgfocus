"""WNF ABI, state decoding and callback lifetime checks without system writes."""
import ctypes
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Windows"))
import dnd


class FakeApi:
    def __init__(self):
        self.profile, self.length, self.status = 0, 4, 0
        self.subscribe_status = self.unsubscribe_status = 0
        self.callback = None
        self.order = []
        self.on_subscribe = self.on_unsubscribe = None
        self.subscribe = Mock(side_effect=self._subscribe)
        self.unsubscribe = Mock(side_effect=self._unsubscribe)
        self.query = Mock(side_effect=self._query)

    def _subscribe(self, handle, name, stamp, callback, context, type_id, group, flags):
        self.order.append("subscribe")
        if not self.subscribe_status:
            handle._obj.value = 1234
        self.callback = callback
        if self.on_subscribe:
            self.on_subscribe()
        return self.subscribe_status

    def _unsubscribe(self, handle):
        self.order.append("unsubscribe")
        if self.on_unsubscribe:
            self.on_unsubscribe()
        return self.unsubscribe_status

    def _query(self, name, type_id, scope, stamp, buffer, length):
        self.order.append("query")
        if name._obj.value != dnd.STATE_NAME or length._obj.value != 4:
            raise AssertionError("Wrong state/buffer ABI")
        if type_id is not None or scope is not None:
            raise AssertionError("Unexpected scope")
        buffer._obj.value = self.profile
        length._obj.value = self.length
        return self.status

    def emit(self, name=dnd.STATE_NAME):
        # Invalid payload pointer is intentional: callbacks must not dereference it.
        return self.callback(name, 1, None, None, 1, 9999)


class WnfTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.changed = Mock()
        self.monitor = dnd.WnfDndMonitor(self.changed)
        self.patch = patch("dnd.WnfApi", return_value=self.api)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.monitor.close)

    def test_initial_state_is_queried_after_subscription_even_if_already_active(self):
        self.api.on_subscribe = lambda: setattr(self.api, "profile", 1)
        self.assertTrue(self.monitor.start())
        self.assertIs(self.monitor.read()[0], True)
        self.assertEqual(self.api.order, ["subscribe", "query"])
        self.assertTrue(self.monitor.start())
        self.api.subscribe.assert_called_once()

    def test_known_profiles_are_strict_booleans_and_future_layouts_unknown(self):
        self.monitor.start()
        for value, expected in [(0, False), (1, True), (2, True), (3, None), (0xFFFFFFFF, None)]:
            self.api.profile = value
            self.assertIs(self.monitor.read()[0], expected)
        self.api.profile = 1
        for length in (0, 1, 8, 100000):
            self.api.length = length
            self.assertIsNone(self.monitor.read()[0])
        self.api.length, self.api.status = 4, -1073741789
        self.assertIsNone(self.monitor.read()[0])
        self.api.query.side_effect = OSError("PRIVATE SYSTEM DATA")
        active, message = self.monitor.read()
        self.assertIsNone(active)
        self.assertNotIn("PRIVATE", message)

    def test_callbacks_coalesce_on_native_thread_and_never_query_or_trust_payload(self):
        self.monitor.start()
        thread = threading.Thread(target=lambda: [self.api.emit() for _ in range(100)])
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.changed.assert_called_once()
        self.api.query.assert_not_called()
        self.monitor.read()
        self.api.emit(name=0)
        self.changed.assert_called_once()
        self.api.emit()
        self.assertEqual(self.changed.call_count, 2)

    def test_unsubscribe_suppresses_callbacks_and_can_restart_without_accumulation(self):
        self.monitor.start()
        self.api.on_unsubscribe = self.api.emit
        self.assertTrue(self.monitor.close())  # Must not deadlock on the callback lock.
        self.api.emit()
        self.changed.assert_not_called()
        self.assertIsNone(self.monitor.read()[0])
        self.api.query.assert_not_called()
        self.assertTrue(self.monitor.close())
        self.api.unsubscribe.assert_called_once()
        self.assertTrue(self.monitor.start())
        self.assertEqual(self.api.subscribe.call_count, 2)

    def test_failed_unsubscribe_retains_native_callback_and_refuses_duplicate_subscription(self):
        self.monitor.start()
        self.api.unsubscribe_status = -1
        self.assertFalse(self.monitor.close())
        self.assertIn(self.monitor, dnd._failed_unsubscriptions)
        self.assertFalse(self.monitor.start())
        self.api.subscribe.assert_called_once()
        self.api.emit()
        self.changed.assert_not_called()
        self.api.unsubscribe_status = 0
        self.assertTrue(self.monitor.close())
        self.assertNotIn(self.monitor, dnd._failed_unsubscriptions)

    def test_missing_symbols_or_subscription_failure_pause_without_polling_fallback(self):
        with patch("dnd.WnfApi", side_effect=AttributeError("private details")):
            self.assertFalse(self.monitor.start())
        self.assertIsNone(self.monitor.read()[0])
        self.api.subscribe_status = -1
        self.assertFalse(self.monitor.start())
        self.assertIsNone(self.monitor.read()[0])
        self.api.query.assert_not_called()


class NativeAbiTests(unittest.TestCase):
    def test_ntdll_bindings_use_windows_widths_even_on_linux_test_host(self):
        library = SimpleNamespace(NtQueryWnfStateData=Mock(), RtlSubscribeWnfStateChangeNotification=Mock(),
                                  RtlUnsubscribeWnfStateChangeNotification=Mock())
        with patch.object(dnd.sys, "platform", "win32"), \
                patch.object(dnd.ctypes, "WinDLL", return_value=library, create=True):
            api = dnd.WnfApi()
        self.assertEqual(ctypes.sizeof(api.query.restype), 4)
        self.assertEqual(ctypes.sizeof(api.subscribe.argtypes[1]), 8)
        self.assertEqual(ctypes.sizeof(api.subscribe.argtypes[2]), 4)
        self.assertEqual(ctypes.sizeof(api.query.argtypes[-1]._type_), 4)
        self.assertEqual(len(api.query.argtypes), 6)
        self.assertEqual(len(api.subscribe.argtypes), 8)

    def test_resume_messages_are_filtered_without_consuming_windows_events(self):
        value = dnd._Message()
        pointer = ctypes.addressof(value)
        self.assertFalse(dnd.is_resume_message(pointer))
        value.message = 0x0218
        for code in (7, 18):
            value.wparam = code
            self.assertTrue(dnd.is_resume_message(pointer))
        value.wparam = 4  # Suspend, not resume.
        self.assertFalse(dnd.is_resume_message(pointer))
        self.assertFalse(dnd.is_resume_message(0))


if __name__ == "__main__":
    unittest.main()
