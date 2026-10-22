"""Read-only, undocumented WNF DND observer. No profile changes or polling.

ABI references: winsiderss/phnt ntexapi.h / ntrtl.h. The shell state name
and 0/1/2 profile values are also used by Workrave's WindowsFocusAssist.
Unknown layouts/statuses deliberately return None; this is not a public API.
"""
import ctypes
import sys
import threading


STATE_NAME = 0x0D83063EA3BF1C75  # WNF_SHEL_QUIETHOURS_ACTIVE_PROFILE_CHANGED
_U32 = ctypes.c_uint32
_I32 = ctypes.c_int32
_U64 = ctypes.c_uint64
_CALLBACK = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    _I32, _U64, _U32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, _U32)
_UNKNOWN = (None, "Не удалось определить «Не беспокоить» через WNF. Доставка приостановлена; доступен ручной режим.")
# A failed unsubscribe must never free a callback that native code still owns.
# The same observer refuses a new subscription until its old one is released.
_failed_unsubscriptions = set()


class WnfApi:
    def __init__(self):
        if sys.platform != "win32":
            raise OSError("WNF requires Windows")
        self.library = ctypes.WinDLL("ntdll")
        self.query = self.library.NtQueryWnfStateData
        self.query.argtypes = [ctypes.POINTER(_U64), ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.POINTER(_U32), ctypes.c_void_p, ctypes.POINTER(_U32)]
        self.query.restype = _I32
        self.subscribe = self.library.RtlSubscribeWnfStateChangeNotification
        self.subscribe.argtypes = [ctypes.POINTER(ctypes.c_void_p), _U64, _U32, _CALLBACK,
                                  ctypes.c_void_p, ctypes.c_void_p, _U32, _U32]
        self.subscribe.restype = _I32
        self.unsubscribe = self.library.RtlUnsubscribeWnfStateChangeNotification
        self.unsubscribe.argtypes = [ctypes.c_void_p]
        self.unsubscribe.restype = _I32


class WnfDndMonitor:
    """start/read/close belong to one owner thread; native callbacks only wake it.

    on_change must enqueue owner-thread work, never close/read inline. Native
    callbacks carry no trusted state and at most one wakeup is pending at once.
    """
    def __init__(self, on_change):
        self._on_change = on_change
        self._api = None
        self._handle = ctypes.c_void_p()
        self._lock = threading.Lock()
        self._active = self._pending = False
        self._callback = _CALLBACK(self._changed)

    def start(self):
        if self._active:
            return True
        if self._handle.value and not self.close():
            return False
        try:
            if self._api is None:
                self._api = WnfApi()
            with self._lock:
                self._active, self._pending = True, False
            # Subscribe before the initial read, avoiding a read/subscribe gap.
            status = self._api.subscribe(ctypes.byref(self._handle), STATE_NAME, 0,
                                         self._callback, None, None, 0, 0)
            if status != 0 or not self._handle.value:
                self.close()
                return False
            return True
        except Exception:
            self.close()
            return False

    def _changed(self, state_name, stamp, type_id, context, buffer, length):
        try:
            if state_name != STATE_NAME:
                return 0
            with self._lock:
                if not self._active or self._pending:
                    return 0
                self._pending = True
            # Do not dereference native payloads or call Qt widgets here.
            self._on_change()
        except Exception:
            with self._lock:
                self._pending = False
        return 0

    def read(self):
        with self._lock:
            self._pending = False
            if not self._active:
                return _UNKNOWN
        try:
            name, stamp = _U64(STATE_NAME), _U32()
            profile, length = _U32(), _U32(ctypes.sizeof(_U32))
            status = self._api.query(ctypes.byref(name), None, None, ctypes.byref(stamp),
                                     ctypes.byref(profile), ctypes.byref(length))
            if status != 0 or length.value != 4 or profile.value not in (0, 1, 2):
                return _UNKNOWN
            if profile.value == 0:
                return False, "«Не беспокоить» выключен — доставка TGFocus на паузе (WNF)"
            return True, ("«Не беспокоить» включён (WNF)" if profile.value == 1 else
                          "«Не беспокоить»: только будильники (WNF); Windows может скрывать баннеры TGFocus")
        except Exception:
            return _UNKNOWN

    def close(self):
        with self._lock:
            self._active = self._pending = False
        if not self._handle.value:
            return True
        # Unsubscribe may wait for a callback; never hold its lock here.
        try:
            if self._api.unsubscribe(self._handle) != 0:
                raise OSError("WNF unsubscribe failed")
        except Exception:
            _failed_unsubscriptions.add(self)
            return False
        self._handle = ctypes.c_void_p()
        _failed_unsubscriptions.discard(self)
        return True


class _Point(ctypes.Structure):
    _fields_ = [("x", _I32), ("y", _I32)]


class _Message(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", _U32),
                ("wparam", ctypes.c_size_t), ("lparam", ctypes.c_ssize_t),
                ("time", _U32), ("point", _Point), ("private", _U32)]


def is_resume_message(message):
    """Qt supplies an OS-owned MSG valid only for the native event call."""
    if not message:
        return False
    value = ctypes.cast(int(message), ctypes.POINTER(_Message)).contents
    return value.message == 0x0218 and value.wparam in (7, 18)  # WM_POWERBROADCAST
