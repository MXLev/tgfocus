import ctypes
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Windows"))
import shell_identity as identity


def fake_native_libraries(initialize=0):
    ole = SimpleNamespace(CoInitializeEx=Mock(return_value=initialize), CoUninitialize=Mock(),
        CoCreateInstance=Mock(), CoTaskMemFree=Mock(), PropVariantClear=Mock(return_value=0))
    shell = SimpleNamespace(SHGetKnownFolderPath=Mock(), SHChangeNotify=Mock())
    return ole, shell


class NativeABITests(unittest.TestCase):
    def test_windows_fixed_width_structures_and_known_property_id(self):
        self.assertEqual(ctypes.sizeof(identity._HRESULT), 4)
        self.assertEqual(ctypes.sizeof(identity._ULONG), 4)
        self.assertEqual(ctypes.sizeof(identity._GUID), 16)
        self.assertEqual(ctypes.sizeof(identity._PropertyKey), 20)
        self.assertEqual(ctypes.sizeof(identity._PropVariant), 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)
        self.assertEqual(identity._PropVariant.value.offset, 8)
        self.assertEqual(identity._APP_ID_KEY.pid, 5)
        self.assertEqual(bytes(identity._APP_ID_KEY.fmtid),
                         uuid.UUID("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3").bytes_le)

    def test_signed_hresult_failure_is_never_accepted_as_success(self):
        for value in [-2147467259, 0x80004005]:
            with self.assertRaises(identity.ShortcutIdentityError) as error:
                identity._check(value)
            self.assertIn("0x80004005", str(error.exception))
        identity._check(0)
        identity._check(1)

    def test_real_ctypes_vtable_query_and_release_use_correct_abi(self):
        releases = []
        queries = []
        table = (ctypes.c_void_p * 3)()
        instance = (ctypes.c_void_p * 1)(ctypes.cast(table, ctypes.c_void_p).value)
        address = ctypes.cast(instance, ctypes.c_void_p).value
        @identity._WINCALL(identity._HRESULT, ctypes.c_void_p, ctypes.POINTER(identity._GUID),
                           ctypes.POINTER(ctypes.c_void_p))
        def query(this, guid, output):
            queries.append((this, bytes(guid.contents)))
            output[0] = address
            return 0
        @identity._WINCALL(identity._ULONG, ctypes.c_void_p)
        def release(this):
            releases.append(this)
            return 0
        table[0] = ctypes.cast(query, ctypes.c_void_p).value
        table[2] = ctypes.cast(release, ctypes.c_void_p).value
        with identity._Interface(address) as original:
            with original.query(identity._IID_PROPERTY_STORE):
                pass
        original.close()  # Idempotent; no double Release.
        self.assertEqual(releases, [address, address])
        self.assertEqual(queries, [(address, bytes(identity._IID_PROPERTY_STORE))])

    def test_com_initialize_and_uninitialize_are_balanced_on_failure(self):
        for result, should_uninitialize in [(0, True), (1, True), (identity._RPC_E_CHANGED_MODE, False)]:
            ole, shell = fake_native_libraries(result)
            with self.subTest(result=result), patch.object(identity.sys, "platform", "win32"), \
                 patch.object(identity.ctypes, "WinDLL", side_effect=lambda name, **kw: ole if name == "ole32" else shell,
                              create=True):
                with self.assertRaises(ValueError):
                    with identity._NativeAPI():
                        raise ValueError("operation failed")
            self.assertEqual(ole.CoUninitialize.call_count, int(should_uninitialize))

    def test_known_folder_memory_is_freed_even_when_hresult_fails(self):
        for result in [0, -2147467259]:
            ole, shell = fake_native_libraries()
            directory = ctypes.create_unicode_buffer("C:\\Users\\Example\\Start Menu\\Programs")
            def known_folder(guid, flags, token, output):
                self.assertEqual(bytes(guid._obj), bytes(identity._FOLDERID_PROGRAMS))
                self.assertEqual(flags, 0)
                self.assertIsNone(token)
                output._obj.value = ctypes.cast(directory, ctypes.c_void_p).value
                return result
            shell.SHGetKnownFolderPath.side_effect = known_folder
            with patch.object(identity.sys, "platform", "win32"), patch.object(identity.ctypes, "WinDLL",
                    side_effect=lambda name, **kw: ole if name == "ole32" else shell, create=True):
                if result:
                    with self.assertRaises(identity.ShortcutIdentityError):
                        identity.get_programs_directory()
                else:
                    self.assertIn("Programs", str(identity.get_programs_directory()))
            ole.CoTaskMemFree.assert_called_once()
            ole.CoUninitialize.assert_called_once()


class ShortcutFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.executable = self.directory / "TGFocus.exe"
        self.executable.write_bytes(b"test executable")
        self.shortcut = self.directory / "TGFocus.lnk"
        self.api = MagicMock()
        self.api.__enter__.return_value = self.api

    def test_atomic_repair_and_moved_executable_path(self):
        self.shortcut.write_bytes(b"old shortcut")
        writes = []
        def write(api, path, executable, arguments, app_id, icon):
            writes.append((executable, arguments, app_id))
            path.write_bytes(b"new shortcut")
        with patch("shell_identity._NativeAPI", return_value=self.api), \
             patch("shell_identity._write_shortcut", side_effect=write):
            result = identity.create_shortcut(self.shortcut, self.executable, '--settings "test"')
        self.assertEqual(result, self.shortcut)
        self.assertEqual(self.shortcut.read_bytes(), b"new shortcut")
        self.assertEqual(writes, [(str(self.executable.resolve()), '--settings "test"', identity.APP_ID)])
        self.assertEqual(list(self.directory.glob(".tgfocus-link-*")), [])
        event, flags, *_ = self.api.shell.SHChangeNotify.call_args.args
        self.assertEqual((event, flags), (0x2000, 5))

    def test_failed_native_write_preserves_existing_shortcut(self):
        self.shortcut.write_bytes(b"old shortcut")
        with patch("shell_identity._NativeAPI", return_value=self.api), \
             patch("shell_identity._write_shortcut", side_effect=OSError("PRIVATE ARGUMENTS")):
            with self.assertRaises(identity.ShortcutIdentityError) as error:
                identity.create_shortcut(self.shortcut, self.executable)
        self.assertNotIn("PRIVATE ARGUMENTS", str(error.exception))
        self.assertEqual(self.shortcut.read_bytes(), b"old shortcut")
        self.assertEqual(list(self.directory.glob(".tgfocus-link-*")), [])
        self.api.shell.SHChangeNotify.assert_not_called()

    def test_unrelated_shortcut_is_not_overwritten(self):
        other = self.directory / "Other.lnk"
        other.write_bytes(b"unrelated")
        with self.assertRaises(identity.ShortcutIdentityError):
            identity.create_shortcut(other, self.executable)
        self.assertEqual(other.read_bytes(), b"unrelated")

    def test_property_store_writes_aumid_and_releases_interfaces(self):
        link, properties, persist = MagicMock(), MagicMock(), MagicMock()
        for interface in (link, properties, persist):
            interface.__enter__.return_value = interface
            interface.invoke.return_value = 0
        link.query.side_effect = [properties, persist]
        self.api.shell_link.return_value = link
        captured = []
        def property_invoke(index, types, *arguments):
            if index == 6:
                key = arguments[0]._obj
                value = arguments[1]._obj
                self.assertEqual(key.pid, 5)
                self.assertEqual(value.vt, 31)
                captured.append(ctypes.wstring_at(value.value.pointer))
            return 0
        properties.invoke.side_effect = property_invoke
        identity._write_shortcut(self.api, self.shortcut, str(self.executable), "--settings", identity.APP_ID, None)
        self.assertEqual(captured, [identity.APP_ID])
        self.assertEqual(link.invoke.call_args_list[0].args[0], 20)
        self.assertEqual(persist.invoke.call_args.args, (6, (ctypes.c_wchar_p, ctypes.c_int32), str(self.shortcut), 1))
        properties.__exit__.assert_called_once()
        persist.__exit__.assert_called_once()
        link.__exit__.assert_called_once()

    def test_read_clears_native_property_string_and_never_resolves(self):
        link, properties, persist = MagicMock(), MagicMock(), MagicMock()
        for interface in (link, properties, persist):
            interface.__enter__.return_value = interface
            interface.invoke.return_value = 0
        self.api.shell_link.return_value = link
        link.query.side_effect = [persist, properties]
        native_string = ctypes.create_unicode_buffer(identity.APP_ID)
        def query_property(index, types, *arguments):
            value = arguments[1]._obj
            value.vt = 31
            value.value.pointer = ctypes.cast(native_string, ctypes.c_void_p)
            return 0
        properties.invoke.side_effect = query_property
        def query_link(index, types, *arguments):
            if index == 3:
                arguments[0].value = str(self.executable)
                self.assertEqual(arguments[3], 4)
            elif index == 10:
                arguments[0].value = "--settings"
            else:
                self.fail("Reader must not resolve or change the shortcut")
            return 0
        link.invoke.side_effect = query_link
        with patch("shell_identity._NativeAPI", return_value=self.api):
            result = identity.read_shortcut(self.shortcut)
        self.assertEqual(result, {"executable": str(self.executable), "arguments": "--settings", "app_id": identity.APP_ID})
        self.api.ole.PropVariantClear.assert_called_once()


if __name__ == "__main__": unittest.main()
