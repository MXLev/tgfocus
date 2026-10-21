import ctypes
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

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


class FocusTests(unittest.TestCase):
    def query(self, manager):
        with patch.object(platform_services.sys, "platform", "win32"), \
             patch("platform_services._focus_manager_type", return_value=manager):
            return platform_services.read_focus_state()

    def test_focus_session_on_and_off_are_real_booleans(self):
        for active in [True, False]:
            manager = SimpleNamespace(is_supported=True, get_default=lambda: SimpleNamespace(is_focus_active=active))
            value, message = self.query(manager)
            self.assertIs(value, active)
            if not active:
                self.assertIn("не статус", message)

    def test_unsupported_focus_and_query_failures_remain_unknown(self):
        self.assertIsNone(self.query(SimpleNamespace(is_supported=False))[0])
        manager = SimpleNamespace(is_supported=True, get_default=Mock(side_effect=OSError("PRIVATE DATA")))
        active, message = self.query(manager)
        self.assertIsNone(active)
        self.assertNotIn("PRIVATE DATA", message)


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

    def test_retained_callbacks_are_bounded(self):
        for _ in range(100):
            self.assertTrue(self.service.show("title", "message"))
        self.assertEqual(len(self.service._retained), 64)
        self.service.close()
        self.assertFalse(self.service.available)
        self.assertEqual(len(self.service._retained), 0)


if __name__ == "__main__": unittest.main()
