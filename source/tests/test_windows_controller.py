import copy
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
except ImportError:
    raise unittest.SkipTest("Windows UI tests require PySide6; run in the Windows build environment")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Windows"))
from controller import Controller
from storage import DEFAULT_CONFIG
from window import SettingsWindow


class FakeBridge(QObject):
    event = Signal(dict)
    error = Signal(str)
    stopped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.commands = []

    def send(self, command):
        self.commands.append(copy.deepcopy(command))
        return True

    def start(self): pass
    def stop(self): pass


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        for target, value in [("controller.WorkerBridge", FakeBridge),
                              ("controller.WnfDndMonitor", Mock(return_value=Mock(start=Mock(return_value=True),
                                  read=Mock(return_value=(None, "Unknown")), close=Mock(return_value=True)))),
                              ("controller.NotificationService", Mock(return_value=Mock(show=Mock(return_value=True),
                                  status_text="Уведомления разрешены", repair=Mock(return_value=True)))),
                              ("controller.CredentialVault", Mock(return_value=Mock(load=Mock(return_value={}), save=Mock())))]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = Controller(Path(self.temporary.name))
        self.addCleanup(self.controller.shutdown)
        self.controller.receive({"type": "ready"})

    def test_runtime_privacy_updates_survive_config_write_failure(self):
        self.controller._secrets = {"aiKeys": {"openrouter": "TEST_KEY"}}
        self.controller.update_config({"enabled": True, "aiEnabled": True})
        self.assertEqual(self.controller.bridge.commands[-1]["config"]["aiApiKey"], "TEST_KEY")
        self.controller.config_store.save = Mock(side_effect=OSError())
        self.controller.update_config({"aiEnabled": False, "aiExcludedChats": [{"id": "42", "name": "Private"}]})
        sent = self.controller.bridge.commands[-1]["config"]
        self.assertEqual(sent["aiApiKey"], "")
        self.assertEqual(sent["aiExcludedChats"][0]["id"], "42")
        self.assertIn("не сохранены", self.controller.error_text)

    def test_unknown_focus_pauses_processing_and_late_messages(self):
        self.controller.update_config({"enabled": True, "windowsFocusAutomation": True})
        self.assertFalse(self.controller.effective_filter_enabled)
        self.assertFalse(self.controller.bridge.commands[-1]["config"]["processingEnabled"])
        self.controller.receive({"type": "message", "allowed": True, "text": "private"})
        self.controller.notifications.show.assert_not_called()
        self.controller.dnd.read.return_value = (True, "DND on")
        self.controller._refresh_focus()
        self.assertTrue(self.controller.effective_filter_enabled)
        self.controller.set_filter_enabled(False)
        self.assertFalse(self.controller.effective_filter_enabled)
        self.controller.dnd.close.assert_called()

    def test_secret_save_failure_never_sends_new_credential(self):
        self.controller.vault.save.side_effect = OSError("private OS error")
        self.controller.connect_telegram("123", "a" * 32)
        self.assertEqual(len(self.controller.bridge.commands), 1)  # Initial config only.
        self.assertNotIn("private OS error", self.controller.error_text)
        self.assertEqual(self.controller._secrets, {})
        self.controller.connect_telegram("1" * 5000, "a" * 32)
        self.assertEqual(len(self.controller.bridge.commands), 1)

    def test_registration_repair_reports_failure_without_changing_filter_or_ai_settings(self):
        original = copy.deepcopy(self.controller.config)
        self.controller.notifications.repair.return_value = False
        self.controller.notifications.error = "Не удалось зарегистрировать TGFocus"
        self.controller.repair_notifications()
        self.assertEqual(self.controller.config, original)
        self.assertEqual(self.controller.error_text, "Не удалось зарегистрировать TGFocus")
        self.controller.notifications.repair.return_value = True
        self.controller.repair_notifications()
        self.assertEqual(self.controller.error_text, "")
        self.controller.error_text = "Ошибка соединения Telegram"
        self.controller.repair_notifications()
        self.assertEqual(self.controller.error_text, "Ошибка соединения Telegram")

    def test_test_notification_preserves_safe_native_error_and_clears_it_after_success(self):
        self.controller.notifications.show.return_value = False
        self.controller.notifications.error = "Не удалось прочитать разрешения (код Windows 0x80070005)."
        self.controller.test_notification()
        self.assertEqual(self.controller.error_text, self.controller.notifications.error)
        self.controller.notifications.show.return_value = True
        self.controller.test_notification()
        self.assertEqual(self.controller.error_text, "")

    def test_native_notification_failures_are_queued_and_old_generation_is_ignored(self):
        self.controller.notifications.generation = 3
        self.controller._notification_error.emit(2, "Old failure")
        self.application.processEvents()
        self.assertEqual(self.controller.error_text, "")
        thread = threading.Thread(target=lambda: self.controller._notification_error.emit(3, "Failure 0x80070005"))
        thread.start()
        thread.join(timeout=2)
        self.assertEqual(self.controller.error_text, "")
        self.application.processEvents()
        self.assertEqual(self.controller.error_text, "Failure 0x80070005")
        self.controller.shutdown()
        self.controller._notification_error.emit(3, "Late failure")
        self.application.processEvents()
        self.assertEqual(self.controller.error_text, "Failure 0x80070005")

    def test_focus_is_checked_again_before_delivering_cached_allowed_message(self):
        self.controller.dnd.read.return_value = (True, "DND on")
        self.controller.update_config({"enabled": True, "windowsFocusAutomation": True})
        self.controller.dnd.read.return_value = (False, "DND ended")
        self.controller.receive({"type": "message", "allowed": True, "text": "late result"})
        self.controller.notifications.show.assert_not_called()
        self.assertFalse(self.controller.effective_filter_enabled)
        self.assertFalse(self.controller.bridge.commands[-1]["config"]["processingEnabled"])

    def test_native_callback_updates_worker_only_on_gui_thread_and_respects_manual_off(self):
        self.controller.dnd.read.return_value = (False, "DND off")
        self.controller.update_config({"enabled": True, "windowsFocusAutomation": True})
        self.controller.dnd.read.return_value = (True, "DND on")
        self.controller.dnd.read.reset_mock()
        thread = threading.Thread(target=self.controller._dnd_dirty.emit)
        thread.start()
        thread.join(timeout=2)
        self.controller.dnd.read.assert_not_called()
        self.application.processEvents()
        self.assertTrue(self.controller.effective_filter_enabled)
        self.assertTrue(self.controller.bridge.commands[-1]["config"]["processingEnabled"])
        self.controller.set_filter_enabled(False)
        self.controller.dnd.read.reset_mock()
        self.controller._dnd_dirty.emit()
        self.application.processEvents()
        self.controller.dnd.read.assert_not_called()
        self.assertFalse(self.controller.effective_filter_enabled)

    def test_resume_refreshes_state_and_shutdown_ignores_queued_wakeups(self):
        self.controller.dnd.read.return_value = (False, "DND off")
        self.controller.update_config({"enabled": True, "windowsFocusAutomation": True})
        self.controller.dnd.read.return_value = (True, "DND on after resume")
        self.controller._resume_filter.wake()
        self.application.processEvents()
        self.assertTrue(self.controller.effective_filter_enabled)
        self.controller._dnd_dirty.emit()
        self.controller.shutdown()
        self.controller.dnd.read.reset_mock()
        self.application.processEvents()
        self.controller.dnd.read.assert_not_called()

    def test_ai_failure_is_silent_and_click_opens_settings(self):
        self.controller.update_config({"aiEnabled": True})
        pages = []
        self.controller.settings_requested.connect(pages.append)
        for _ in range(3):
            self.controller.receive({"type": "aiStatus", "connection": "failed", "message": "Fixed error"})
        self.controller.notifications.show.assert_called_once()
        request = self.controller.notifications.show.call_args
        self.assertTrue(request.kwargs["silent"])
        request.kwargs["on_activate"]()
        self.assertEqual(pages, ["ai"])
        self.controller.update_config({"aiEnabled": False})
        self.controller.receive({"type": "aiStatus", "connection": "failed", "message": "late"})
        self.controller.notifications.show.assert_called_once()

    def test_preview_sound_and_history_do_not_retain_message_body(self):
        self.controller.update_config({"enabled": True, "showPreview": False, "playSound": False})
        self.controller.receive({"type": "message", "allowed": True, "title": "Chat", "text": "PRIVATE_BODY"})
        request = self.controller.notifications.show.call_args
        self.assertNotIn("PRIVATE_BODY", str(request))
        self.assertTrue(request.kwargs["silent"])
        self.assertNotIn("PRIVATE_BODY", str(self.controller.activities))
        for _ in range(70):
            self.controller.receive({"type": "message", "allowed": False, "title": "Chat"})
        self.assertEqual(len(self.controller.activities), 50)

    def test_window_hides_reopens_and_clears_ephemeral_secrets(self):
        window = SettingsWindow(self.controller)
        self.addCleanup(window.deleteLater)
        window.show_page("connection")
        window.api_hash.setText("TEST_HASH")
        window.code.setText("12345")
        window.password.setText("TEST_PASSWORD")
        window.ai_key.setText("TEST_AI")
        self.assertTrue(window.isVisible())
        window.close()
        self.assertFalse(window.isVisible())
        self.assertTrue(all(not field.text() for field in (window.api_hash, window.code, window.password, window.ai_key)))
        window.show_page("ai")
        self.assertTrue(window.isVisible())
        window.close()


if __name__ == "__main__": unittest.main()
