"""Native Windows notifications."""
import ctypes
import sys
import threading
from collections import deque
from urllib.parse import urlsplit

APP_ID = "MXLev.TGFocus.Windows"
_apartment = threading.local()


def _ensure_winrt():
    if getattr(_apartment, "initialized", False):
        return
    from winrt.runtime import ApartmentType, init_apartment
    try:
        init_apartment(ApartmentType.SINGLE_THREADED)
    except OSError as error:
        # Qt or a host can already have initialized this thread as MTA.
        if getattr(error, "winerror", None) not in (-2147417850, 0x80010106):
            raise
    _apartment.initialized = True


def _register_app_id():
    import winreg
    path = "Software\\Classes\\AppUserModelId\\" + APP_ID
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "TGFocus")
        winreg.SetValueEx(key, "ShowInSettings", 0, winreg.REG_DWORD, 1)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
    shell.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
    if shell.SetCurrentProcessExplicitAppUserModelID(APP_ID) < 0:
        raise OSError("Cannot set application identity")


def _notification_types():
    _ensure_winrt()
    from windows_toasts import InteractableWindowsToaster, Toast, ToastAudio
    from winrt.windows.ui.notifications import NotificationSetting
    return InteractableWindowsToaster, Toast, ToastAudio, NotificationSetting


class NotificationService:
    def __init__(self):
        self.available = False
        self.error = ""
        self._toaster = None
        self._retained = deque(maxlen=64)
        if sys.platform != "win32":
            self.error = "Уведомления Windows недоступны на этой платформе."
            return
        try:
            _register_app_id()
            toaster_type, self._toast_type, self._audio_type, self._settings_type = _notification_types()
            self._toaster = toaster_type("TGFocus", notifierAUMID=APP_ID)
            self.available = True
        except Exception:
            self.error = "Не удалось подготовить уведомления Windows."

    def show(self, title, body, *, subtitle="", silent=False, url="", on_activate=None):
        if not self.available:
            return False
        try:
            if self._toaster.toastNotifier.setting != self._settings_type.ENABLED:
                self.error = "Уведомления TGFocus запрещены настройками Windows."
                return False
            if url and urlsplit(url).scheme != "tg":
                self.error = "Неподдерживаемая ссылка уведомления."
                return False
            fields = [str(title)[:256]]
            if subtitle:
                fields.append(str(subtitle)[:256])
            fields.append(str(body)[:1000])
            toast = self._toast_type(text_fields=fields, audio=self._audio_type(silent=bool(silent)),
                launch_action=url or None,
                on_activated=(lambda _args: on_activate()) if on_activate and not url else None)
            self._toaster.show_toast(toast)
            self._retained.append(toast)
            self.error = ""
            # Accepted by Windows; Focus/notification settings still determine banners.
            return True
        except Exception:
            self.error = "Windows не приняла уведомление TGFocus."
            return False

    def clear(self):
        if self._toaster:
            try:
                self._toaster.clear_toasts()
            except Exception:
                self.error = "Не удалось очистить уведомления TGFocus."
        self._retained.clear()

    def close(self):
        self._retained.clear()
        self._toaster = None
        self.available = False
