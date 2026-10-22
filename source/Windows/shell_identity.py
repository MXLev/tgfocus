"""Per-user Start Menu identity for legacy desktop WinRT notifications.

Only a Shell Link with PKEY_AppUserModel_ID is created. No COM activation server,
elevation, task, service or startup entry is registered by this module.
"""
import ctypes
import os
import sys
import tempfile
import uuid
from pathlib import Path

APP_ID = "MXLev.TGFocus.Windows"
_HRESULT = ctypes.c_int32
_ULONG = ctypes.c_uint32
_WINCALL = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_RPC_E_CHANGED_MODE = -2147417850
_VT_EMPTY = 0
_VT_LPWSTR = 31


class ShortcutIdentityError(OSError):
    pass


class _GUID(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", ctypes.c_uint32)]


class _CountedPointer(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("pointer", ctypes.c_void_p)]


class _VariantValue(ctypes.Union):
    _fields_ = [("pointer", ctypes.c_void_p), ("integer", ctypes.c_int64), ("counted", _CountedPointer)]


class _PropVariant(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_uint16), ("reserved1", ctypes.c_uint16),
                ("reserved2", ctypes.c_uint16), ("reserved3", ctypes.c_uint16), ("value", _VariantValue)]


_CLSID_SHELL_LINK = _GUID.parse("00021401-0000-0000-c000-000000000046")
_IID_SHELL_LINK_W = _GUID.parse("000214f9-0000-0000-c000-000000000046")
_IID_PERSIST_FILE = _GUID.parse("0000010b-0000-0000-c000-000000000046")
_IID_PROPERTY_STORE = _GUID.parse("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")
_FOLDERID_PROGRAMS = _GUID.parse("a77f5d77-2e2b-44c3-a6a2-aba601054a51")
_APP_ID_KEY = _PropertyKey(_GUID.parse("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3"), 5)


def _check(result):
    # Explicit signed 32-bit interpretation is also important for mocked HRESULTs.
    value = ctypes.c_int32(result).value
    if value < 0:
        raise ShortcutIdentityError("Ошибка Windows при настройке ярлыка TGFocus (0x%08X)." % (value & 0xFFFFFFFF))


class _Interface:
    def __init__(self, pointer):
        self.pointer = ctypes.c_void_p(pointer.value if isinstance(pointer, ctypes.c_void_p) else pointer)
        if not self.pointer.value:
            raise ShortcutIdentityError("Windows не предоставила интерфейс ярлыка TGFocus.")

    def invoke(self, index, argument_types, *arguments, result_type=_HRESULT):
        table = ctypes.cast(self.pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        method = _WINCALL(result_type, ctypes.c_void_p, *argument_types)(table[index])
        return method(self.pointer, *arguments)

    def query(self, identifier):
        pointer = ctypes.c_void_p()
        result = self.invoke(0, (ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)),
                             ctypes.byref(identifier), ctypes.byref(pointer))
        if ctypes.c_int32(result).value < 0:
            if pointer.value:
                _Interface(pointer).close()
            _check(result)
        return _Interface(pointer)

    def close(self):
        if self.pointer.value:
            try:
                self.invoke(2, (), result_type=_ULONG)
            finally:
                self.pointer = ctypes.c_void_p()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _NativeAPI:
    def __init__(self):
        if sys.platform != "win32":
            raise ShortcutIdentityError("Ярлык Windows доступен только на Windows.")
        self.ole = ctypes.WinDLL("ole32", use_last_error=True)
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        self.ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.ole.CoInitializeEx.restype = _HRESULT
        self.ole.CoUninitialize.argtypes = []
        self.ole.CoUninitialize.restype = None
        self.ole.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_uint32,
                                            ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        self.ole.CoCreateInstance.restype = _HRESULT
        self.ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        self.ole.CoTaskMemFree.restype = None
        self.ole.PropVariantClear.argtypes = [ctypes.POINTER(_PropVariant)]
        self.ole.PropVariantClear.restype = _HRESULT
        self.shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(_GUID), ctypes.c_uint32, ctypes.c_void_p,
                                                   ctypes.POINTER(ctypes.c_void_p)]
        self.shell.SHGetKnownFolderPath.restype = _HRESULT
        self.shell.SHChangeNotify.argtypes = [ctypes.c_int32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
        self.shell.SHChangeNotify.restype = None
        self._uninitialize = False

    def __enter__(self):
        result = ctypes.c_int32(self.ole.CoInitializeEx(None, 2)).value  # STA, or use an existing MTA.
        if result == _RPC_E_CHANGED_MODE:
            return self
        _check(result)
        self._uninitialize = True  # Both S_OK and S_FALSE increment the COM reference count.
        return self

    def __exit__(self, *_args):
        if self._uninitialize:
            self.ole.CoUninitialize()
            self._uninitialize = False

    def shell_link(self):
        pointer = ctypes.c_void_p()
        result = self.ole.CoCreateInstance(ctypes.byref(_CLSID_SHELL_LINK), None, 1,
                                           ctypes.byref(_IID_SHELL_LINK_W), ctypes.byref(pointer))
        if ctypes.c_int32(result).value < 0:
            if pointer.value:
                _Interface(pointer).close()
            _check(result)
        return _Interface(pointer)


def get_programs_directory():
    """Return this Windows user's Start Menu Programs folder, without elevation."""
    try:
        with _NativeAPI() as api:
            pointer = ctypes.c_void_p()
            try:
                _check(api.shell.SHGetKnownFolderPath(ctypes.byref(_FOLDERID_PROGRAMS), 0, None,
                                                     ctypes.byref(pointer)))
                if not pointer.value:
                    raise ShortcutIdentityError("Windows не предоставила папку меню «Пуск».")
                return Path(ctypes.wstring_at(pointer.value))
            finally:
                if pointer.value:
                    api.ole.CoTaskMemFree(pointer)
    except ShortcutIdentityError:
        raise
    except Exception:
        raise ShortcutIdentityError("Не удалось определить пользовательскую папку меню «Пуск».") from None


def _write_shortcut(api, path, executable, arguments, app_id, icon):
    with api.shell_link() as link:
        _check(link.invoke(20, (ctypes.c_wchar_p,), executable))
        _check(link.invoke(11, (ctypes.c_wchar_p,), arguments))
        _check(link.invoke(9, (ctypes.c_wchar_p,), str(Path(executable).parent)))
        _check(link.invoke(7, (ctypes.c_wchar_p,), "Telegram Focus"))
        _check(link.invoke(17, (ctypes.c_wchar_p, ctypes.c_int32), icon or executable, 0))
        with link.query(_IID_PROPERTY_STORE) as properties:
            # Caller-owned storage avoids mixing Python allocation with CoTaskMemFree.
            value = _PropVariant()
            text = ctypes.create_unicode_buffer(app_id)
            value.vt = _VT_LPWSTR
            value.value.pointer = ctypes.cast(text, ctypes.c_void_p)
            _check(properties.invoke(6, (ctypes.POINTER(_PropertyKey), ctypes.POINTER(_PropVariant)),
                                     ctypes.byref(_APP_ID_KEY), ctypes.byref(value)))
            _check(properties.invoke(7, ()))
        with link.query(_IID_PERSIST_FILE) as persist:
            _check(persist.invoke(6, (ctypes.c_wchar_p, ctypes.c_int32), str(path), 1))


def create_shortcut(path, executable, arguments="", app_id=APP_ID, icon=None):
    """Atomically repair this app's TGFocus.lnk, including its notification AUMID."""
    try:
        path, executable = Path(path).absolute(), Path(executable).resolve()
        if path.name.casefold() != "tgfocus.lnk" or not executable.is_file():
            raise ShortcutIdentityError("Нужны ярлык TGFocus.lnk и существующий исполняемый файл.")
        if (not isinstance(arguments, str) or not isinstance(app_id, str) or not app_id or len(app_id) > 128 or
                any(char.isspace() for char in app_id) or
                any("\x00" in str(value) for value in (path, executable, arguments, app_id, icon or ""))):
            raise ShortcutIdentityError("Некорректные параметры ярлыка TGFocus.")
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        with _NativeAPI() as api:
            with tempfile.TemporaryDirectory(prefix=".tgfocus-link-", dir=path.parent) as temporary:
                staged = Path(temporary) / "TGFocus.lnk"
                _write_shortcut(api, staged, str(executable), arguments, app_id,
                                str(Path(icon).resolve()) if icon else None)
                os.replace(staged, path)
            # Notify the shell of exactly the changed shortcut; do not refresh unrelated items.
            changed_path = ctypes.create_unicode_buffer(str(path))
            api.shell.SHChangeNotify(0x00002000 if existed else 0x00000002, 0x0005,
                                    ctypes.cast(changed_path, ctypes.c_void_p), None)
        return path
    except ShortcutIdentityError:
        raise
    except Exception:
        raise ShortcutIdentityError("Не удалось создать или обновить ярлык TGFocus в меню «Пуск».") from None


def read_shortcut(path):
    """Read identity without resolving or executing the target of a Shell Link."""
    try:
        with _NativeAPI() as api, api.shell_link() as link:
            with link.query(_IID_PERSIST_FILE) as persist:
                _check(persist.invoke(5, (ctypes.c_wchar_p, ctypes.c_uint32), str(Path(path).absolute()), 0))
            executable = ctypes.create_unicode_buffer(32768)
            arguments = ctypes.create_unicode_buffer(32768)
            _check(link.invoke(3, (ctypes.c_wchar_p, ctypes.c_int32, ctypes.c_void_p, ctypes.c_uint32),
                               executable, len(executable), None, 0x4))  # SLGP_RAWPATH; no Resolve.
            _check(link.invoke(10, (ctypes.c_wchar_p, ctypes.c_int32), arguments, len(arguments)))
            with link.query(_IID_PROPERTY_STORE) as properties:
                value = _PropVariant()
                try:
                    _check(properties.invoke(5, (ctypes.POINTER(_PropertyKey), ctypes.POINTER(_PropVariant)),
                                             ctypes.byref(_APP_ID_KEY), ctypes.byref(value)))
                    if value.vt == _VT_EMPTY:
                        app_id = ""
                    elif value.vt == _VT_LPWSTR and value.value.pointer:
                        app_id = ctypes.wstring_at(value.value.pointer)
                    else:
                        raise ShortcutIdentityError("В ярлыке TGFocus найден неподдерживаемый тип идентификатора.")
                finally:
                    api.ole.PropVariantClear(ctypes.byref(value))
            return {"executable": executable.value, "arguments": arguments.value, "app_id": app_id}
    except ShortcutIdentityError:
        raise
    except Exception:
        raise ShortcutIdentityError("Не удалось прочитать идентификатор ярлыка TGFocus.") from None
