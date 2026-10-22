import ctypes
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Windows"))
import platform_services
import storage
from storage import ConfigStore, CredentialVault, DEFAULT_CONFIG, StorageError, normalize_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.store = ConfigStore(self.directory)

    def test_missing_file_defaults_are_independent_and_disabled(self):
        first, second = self.store.load(), self.store.load()
        self.assertFalse(first["enabled"])
        self.assertFalse(first["aiEnabled"])
        self.assertFalse(first["windowsFocusAutomation"])
        first["people"].append(dict(id="42", name="VIP"))
        self.assertEqual(second["people"], [])
        self.assertEqual(DEFAULT_CONFIG["people"], [])

    def test_roundtrip_and_schema_limits(self):
        config = dict(DEFAULT_CONFIG, enabled=True, aiEnabled=True, windowsFocusAutomation=True,
                      aiHourlyLimit=999, aiInstruction="a" * 3000,
                      people=[dict(id=42, name="VIP")], aiExcludedChats=[dict(id="-10012", name="Private")])
        self.store.save(config)
        loaded = self.store.load()
        self.assertEqual(loaded["people"], [dict(id="42", name="VIP")])
        self.assertEqual(loaded["aiExcludedChats"], config["aiExcludedChats"])
        self.assertEqual(loaded["aiHourlyLimit"], 300)
        self.assertEqual(len(loaded["aiInstruction"]), 2000)
        self.assertTrue(loaded["windowsFocusAutomation"])

    def test_secrets_cannot_be_written_at_any_level(self):
        for config in [dict(DEFAULT_CONFIG, aiApiKey="SECRET"),
                       dict(DEFAULT_CONFIG, session="SECRET"),
                       dict(DEFAULT_CONFIG, unused={"credentials": "SECRET"}),
                       dict(DEFAULT_CONFIG, people=[dict(id="1", name="a", api_hash="SECRET")])]:
            with self.subTest(config=config), self.assertRaises(StorageError) as error:
                self.store.save(config)
            self.assertNotIn("SECRET", str(error.exception))
        self.assertFalse(self.store.path.exists())

    def test_corrupt_config_is_not_overwritten_or_secret_echoed(self):
        original = b'{"password":"SECRET", broken'
        self.store.path.write_bytes(original)
        with self.assertRaises(StorageError) as error:
            self.store.load()
        self.assertNotIn("SECRET", str(error.exception))
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_replace_failure_keeps_existing_settings(self):
        self.store.save(DEFAULT_CONFIG)
        original = self.store.path.read_bytes()
        with patch("storage.os.replace", side_effect=OSError("private path")), self.assertRaises(StorageError):
            self.store.save(dict(DEFAULT_CONFIG, enabled=True))
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertEqual(list(self.directory.glob(".tgfocus-*")), [])

    def test_unknown_fields_and_duplicate_ids_not_preserved(self):
        result = normalize_config(dict(DEFAULT_CONFIG, processingEnabled=True,
            people=[dict(id="42", name="a"), dict(id="42", name="b"), dict(id="invalid", name="c")]))
        self.assertNotIn("processingEnabled", result)
        self.assertEqual(result["people"], [dict(id="42", name="a")])


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.vault = CredentialVault(Path(self.temporary.name))

    def test_vault_stores_only_ciphertext_and_roundtrips(self):
        payload = dict(apiId="123", apiHash="SECRET_HASH", session="SECRET_SESSION", aiKeys={"openrouter": "SECRET_AI"})
        plaintext = json.dumps(payload, ensure_ascii=False).encode()
        def fake_dpapi(data, *, protect):
            if protect:
                self.assertEqual(json.loads(data), payload)
                return b"opaque ciphertext"
            self.assertEqual(data, b"opaque ciphertext")
            return plaintext
        self.assertEqual(self.vault.load(), {})
        with patch("storage._dpapi", side_effect=fake_dpapi):
            self.vault.save(payload)
            self.assertEqual(self.vault.load(), payload)
        stored = self.vault.path.read_bytes()
        self.assertNotIn(b"SECRET", stored)
        self.assertNotIn(b"apiHash", stored)

    def test_decrypt_error_is_redacted_and_preserves_file(self):
        self.vault.path.write_bytes(storage._VAULT_HEADER + b"cipher")
        with patch("storage._dpapi", side_effect=OSError("SECRET_SESSION")), self.assertRaises(StorageError) as error:
            self.vault.load()
        self.assertNotIn("SECRET_SESSION", str(error.exception))
        self.assertTrue(self.vault.path.exists())

    def test_encryption_failure_does_not_write_plaintext(self):
        with patch("storage._dpapi", side_effect=OSError("SECRET")), self.assertRaises(StorageError):
            self.vault.save({"session": "SECRET"})
        self.assertFalse(self.vault.path.exists())

    def test_native_dpapi_uses_current_user_flags_and_frees_output(self):
        output = ctypes.create_string_buffer(b"encrypted")
        protect = Mock()
        def encrypt(incoming, description, entropy, reserved, prompt, flags, outgoing):
            self.assertEqual(flags, 1)  # UI forbidden, not LOCAL_MACHINE.
            self.assertIsNone(entropy)
            blob = outgoing._obj
            blob.size = 9
            blob.data = ctypes.cast(output, ctypes.POINTER(ctypes.c_ubyte))
            return 1
        protect.side_effect = encrypt
        unprotect = Mock()
        free = Mock()
        libs = {"crypt32": SimpleNamespace(CryptProtectData=protect, CryptUnprotectData=unprotect),
                "kernel32": SimpleNamespace(LocalFree=free)}
        with patch.object(storage.sys, "platform", "win32"), patch.object(storage.ctypes, "WinDLL",
                side_effect=lambda name, **kw: libs[name], create=True):
            self.assertEqual(storage._dpapi(b"SECRET", protect=True), b"encrypted")
        free.assert_called_once()


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.toaster = SimpleNamespace(toastNotifier=SimpleNamespace(setting=0), show_toast=Mock(), clear_toasts=Mock())
        self.factory = Mock(return_value=self.toaster)
        self.toast_type = lambda **fields: SimpleNamespace(**fields)
        with patch.object(platform_services.sys, "platform", "win32"), \
             patch("platform_services._register_app_id"), \
             patch("platform_services._notification_types", return_value=(self.factory, self.toast_type,
                   lambda **fields: SimpleNamespace(**fields), SimpleNamespace(ENABLED=0))):
            self.service = platform_services.NotificationService()

    def test_uses_own_identity_and_silent_audio_for_ai_error(self):
        self.factory.assert_called_once_with("TGFocus", notifierAUMID=platform_services.APP_ID)
        activate = Mock()
        self.assertTrue(self.service.show("AI down", "Local fallback", silent=True, on_activate=activate))
        toast = self.toaster.show_toast.call_args.args[0]
        self.assertTrue(toast.audio.silent)
        toast.on_activated(None)
        activate.assert_called_once()

    def test_telegram_click_uses_protocol_and_rejects_other_schemes(self):
        self.assertTrue(self.service.show("hello", "message", url="tg://resolve?domain=test"))
        self.assertEqual(self.toaster.show_toast.call_args.args[0].launch_action, "tg://resolve?domain=test")
        self.assertFalse(self.service.show("hello", "message", url="https://evil.example"))
        self.assertEqual(self.toaster.show_toast.call_count, 1)

    def test_disabled_toasts_or_native_failure_return_false_without_raw_errors(self):
        self.toaster.toastNotifier.setting = 1
        self.assertFalse(self.service.show("t", "b"))
        self.toaster.show_toast.assert_not_called()
        self.toaster.toastNotifier.setting = 0
        self.toaster.show_toast.side_effect = OSError("PRIVATE MESSAGE")
        self.assertFalse(self.service.show("t", "b"))
        self.assertNotIn("PRIVATE MESSAGE", self.service.error)

    def test_status_reports_permissions_and_registration_can_be_repaired(self):
        self.assertIn("Windows разрешает", self.service.status_text)
        self.toaster.toastNotifier.setting = 1
        self.assertIn("выключены", self.service.status_text)
        with patch.object(platform_services.sys, "platform", "win32"), \
                patch("platform_services._register_app_id", side_effect=OSError("PRIVATE PATH")):
            self.assertFalse(self.service.repair())
        self.assertFalse(self.service.available)
        self.assertNotIn("PRIVATE", self.service.status_text)
        self.assertIn("зарегистрировать", self.service.status_text)

    def _fail_permission_query(self, code):
        error = OSError("PRIVATE TEXT THAT MUST NOT REACH UI")
        error.winerror = code

        class UninitializedNotifier:
            @property
            def setting(self):
                raise error

        self.toaster.toastNotifier = UninitializedNotifier()

    def test_unpackaged_missing_profile_attempts_first_show_and_surfaces_submit_failure(self):
        self._fail_permission_query(0x80070490)
        with patch("platform_services._is_unpackaged", return_value=True):
            self.assertIn("ещё не создала", self.service.status_text)
            self.assertTrue(self.service.show("title", "body"))
            self.toaster.show_toast.assert_called_once()
            denied = OSError("PRIVATE BODY")
            denied.winerror = 0x80070005
            self.toaster.show_toast.side_effect = denied
            self.assertFalse(self.service.show("title", "body"))
        self.assertIn("0x80070005", self.service.error)
        self.assertNotIn("PRIVATE", self.service.error)
        self.assertNotIn("0x80070490", self.service.error)

    def test_no_first_show_fallback_for_packaged_senders_other_errors_or_explicit_denials(self):
        for code, unpackaged in [(0x80070490, False), (0x80070005, True), (0x80004005, True)]:
            with self.subTest(code=code, unpackaged=unpackaged):
                self._fail_permission_query(code)
                with patch("platform_services._is_unpackaged", return_value=unpackaged):
                    self.assertFalse(self.service.show("title", "body"))
                self.assertIn(f"0x{code:08X}", self.service.error)
                self.assertNotIn("PRIVATE", self.service.status_text)
        for value in (1, 2, 3, 4, 999):
            self.toaster.toastNotifier = SimpleNamespace(setting=value)
            self.assertFalse(self.service.show("title", "body"))
        self.toaster.show_toast.assert_not_called()

    def test_async_native_failure_reports_only_code_and_ignores_released_toasts(self):
        callback = Mock()
        self.service.on_failure = callback
        self.assertTrue(self.service.show("private title", "private body"))
        toast = self.toaster.show_toast.call_args.args[0]
        toast.on_failed(SimpleNamespace(error_code=-2147024809))
        callback.assert_called_once_with(self.service.generation,
            "Windows отклонила уведомление после отправки (код Windows 0x80070057).")
        self.assertNotIn("private", self.service.error)
        self.service.close()
        toast.on_failed(SimpleNamespace(error_code=0x80070005))
        callback.assert_called_once()

    def test_native_failure_inside_show_is_not_overwritten_with_success(self):
        self.toaster.show_toast.side_effect = lambda toast: toast.on_failed(SimpleNamespace(error_code=0x80070057))
        self.assertFalse(self.service.show("title", "body"))
        self.assertIn("0x80070057", self.service.error)

    def test_identity_failure_does_not_create_notifier_with_unknown_aumid(self):
        with patch.object(platform_services.sys, "platform", "win32"), \
                patch("platform_services._register_app_id", side_effect=OSError()), \
                patch("platform_services._notification_types") as factory:
            service = platform_services.NotificationService()
        factory.assert_not_called()
        self.assertFalse(service.show("title", "body"))

    def test_retained_callbacks_are_bounded(self):
        for _ in range(100):
            self.assertTrue(self.service.show("title", "message"))
        self.assertEqual(len(self.service._retained), 64)
        self.service.close()
        self.assertFalse(self.service.available)
        self.assertEqual(len(self.service._retained), 0)


class RegistrationTests(unittest.TestCase):
    def test_package_detection_only_accepts_native_no_package_status(self):
        query = Mock()
        with patch.object(platform_services.ctypes, "WinDLL",
                          return_value=SimpleNamespace(GetCurrentPackageFullName=query), create=True):
            for code, expected in [(15700, True), (122, False), (5, False), (0, False)]:
                query.return_value = code
                self.assertIs(platform_services._is_unpackaged(), expected)
            self.assertEqual(ctypes.sizeof(query.restype), 4)
            query.side_effect = OSError()
            self.assertFalse(platform_services._is_unpackaged())

    def test_shortcut_and_notifier_share_identity_and_matching_shortcut_is_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "Кириллица и пробелы" / "TGFocus.exe"
            expected = dict(executable=str(executable), arguments="", app_id=platform_services.APP_ID)
            shortcut = Path(temporary) / "TGFocus.lnk"
            registry = SimpleNamespace(HKEY_CURRENT_USER=0, REG_SZ=1, REG_DWORD=4,
                CreateKeyEx=MagicMock(), SetValueEx=Mock())
            identity = SimpleNamespace(get_programs_directory=Mock(return_value=Path(temporary)),
                read_shortcut=Mock(side_effect=[FileNotFoundError(), expected]), create_shortcut=Mock())
            shell = SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=Mock(return_value=0))
            with patch.dict(sys.modules, {"winreg": registry, "shell_identity": identity}), \
                    patch("platform_services._launcher", return_value=(executable, "")), \
                    patch.object(platform_services.ctypes, "WinDLL", return_value=shell, create=True):
                self.assertEqual(platform_services._register_app_id(), shortcut)
                identity.create_shortcut.assert_called_once_with(shortcut, executable, arguments="",
                    app_id=platform_services.APP_ID, icon=executable)
                shell.SetCurrentProcessExplicitAppUserModelID.assert_called_with(platform_services.APP_ID)
                identity.create_shortcut.reset_mock()
                identity.read_shortcut.side_effect = [expected, expected]
                platform_services._register_app_id()
                identity.create_shortcut.assert_not_called()


if __name__ == "__main__": unittest.main()
