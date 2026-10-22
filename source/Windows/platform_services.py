"""Native Windows notifications."""
import ctypes
from pathlib import Path
import subprocess
import sys
import threading
from collections import deque
from urllib.parse import urlsplit

APP_ID = "MXLev.TGFocus.Windows"
_apartment = threading.local()


def _windows_code(error):
    for attribute in ("winerror", "hresult", "errno"):
        value = getattr(error, attribute, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value & 0xFFFFFFFF
    return None


def _safe_error(operation, error):
    # Never expose an OS error string, traceback, toast body, path or credentials.
    code = _windows_code(error)
    if code is not None:
        return f"{operation} (код Windows 0x{code:08X})."
    kind = type(error).__name__
    if kind not in {"OSError", "RuntimeError", "ValueError", "TypeError", "AttributeError", "ModuleNotFoundError"}:
        kind = "Exception"
    return f"{operation} ({kind})."


def _is_unpackaged():
    try:
        kernel = ctypes.WinDLL("kernel32")
        query = kernel.GetCurrentPackageFullName
        query.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p]
        query.restype = ctypes.c_int32
        length = ctypes.c_uint32()
        return query(ctypes.byref(length), None) == 15700  # APPMODEL_ERROR_NO_PACKAGE
    except Exception:
        return False


def _first_toast_profile_missing(error):
    # An unpackaged sender may have no per-app settings until its first Show.
    # Only this specific absence permits trying Show; Windows still enforces
    # permissions. Packaged apps, explicit denials and other errors never bypass.
    return _windows_code(error) in (0x80070490, 1168) and _is_unpackaged()


def _disabled_message(setting):
    return {
        1: "Уведомления TGFocus выключены для приложения в настройках Windows.",
        2: "Уведомления выключены для пользователя Windows.",
        3: "Уведомления TGFocus запрещены политикой устройства Windows.",
        4: "Windows сообщает, что отправитель не поддерживает уведомления (статус 4). Восстанови регистрацию TGFocus.",
    }.get(setting, "Windows вернула неизвестный статус разрешений уведомлений TGFocus.")


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


def _launcher():
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return executable, ""
    windowed = executable.with_name("pythonw.exe")
    if windowed.is_file():
        executable = windowed
    return executable, subprocess.list2cmdline([str(Path(__file__).with_name("app.py").resolve())])


def _register_app_id(*, shortcut_directory=None, app_id=APP_ID):
    import winreg
    from shell_identity import create_shortcut, get_programs_directory, read_shortcut
    executable, arguments = _launcher()
    shortcut = (Path(shortcut_directory) if shortcut_directory is not None else get_programs_directory()) / "TGFocus.lnk"
    expected = dict(executable=str(executable), arguments=arguments, app_id=app_id)
    try:
        existing = read_shortcut(shortcut)
    except Exception:
        existing = None
    if existing != expected:
        create_shortcut(shortcut, executable, arguments=arguments, app_id=app_id, icon=executable)
    if read_shortcut(shortcut) != expected:
        raise OSError("Application shortcut identity mismatch")
    path = "Software\\Classes\\AppUserModelId\\" + app_id
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "TGFocus")
        winreg.SetValueEx(key, "ShowInSettings", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(executable))
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
    shell.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
    if shell.SetCurrentProcessExplicitAppUserModelID(app_id) < 0:
        raise OSError("Cannot set application identity")
    return shortcut


def _notification_types():
    _ensure_winrt()
    from windows_toasts import InteractableWindowsToaster, Toast, ToastAudio
    from winrt.windows.ui.notifications import NotificationSetting
    return InteractableWindowsToaster, Toast, ToastAudio, NotificationSetting


class NotificationService:
    def __init__(self, *, register_identity=True):
        self.available = False
        self.error = ""
        self._toaster = None
        self._retained = deque(maxlen=64)
        self._register_identity = register_identity
        self.generation = 0
        self.on_failure = None
        if sys.platform != "win32":
            self.error = "Уведомления Windows недоступны на этой платформе."
            return
        self.repair()

    def repair(self):
        self.close()
        if sys.platform != "win32":
            self.error = "Уведомления Windows недоступны на этой платформе."
            return False
        try:
            if self._register_identity:
                _register_app_id()
        except Exception:
            self.error = "Не удалось зарегистрировать TGFocus в меню «Пуск». Проверь доступ к папке программы и нажми «Восстановить регистрацию»."
            return False
        try:
            toaster_type, self._toast_type, self._audio_type, self._settings_type = _notification_types()
            self._toaster = toaster_type("TGFocus", notifierAUMID=APP_ID)
            self.available = True
            self.error = ""
            return True
        except Exception as error:
            self.error = _safe_error("Не удалось подготовить уведомления Windows", error)
            return False

    @property
    def status_text(self):
        if not self.available:
            return self.error or "Уведомления Windows не готовы."
        try:
            setting = self._toaster.toastNotifier.setting
            if setting == self._settings_type.ENABLED:
                return "Windows разрешает уведомления TGFocus. Для баннеров во время «Не беспокоить» добавь TGFocus в приоритетные приложения."
            return _disabled_message(setting)
        except Exception as error:
            if _first_toast_profile_missing(error):
                return "Windows ещё не создала настройки TGFocus. При выключенном «Не беспокоить» нажми «Тестовое уведомление Windows»."
            return _safe_error("Не удалось прочитать разрешения уведомлений Windows", error)

    def show(self, title, body, *, subtitle="", silent=False, url="", on_activate=None):
        if not self.available:
            return False
        try:
            setting = self._toaster.toastNotifier.setting
        except Exception as error:
            if not _first_toast_profile_missing(error):
                self.error = _safe_error("Не удалось прочитать разрешения уведомлений Windows", error)
                return False
        else:
            if setting != self._settings_type.ENABLED:
                self.error = _disabled_message(setting)
                return False
        try:
            if url and urlsplit(url).scheme != "tg":
                self.error = "Неподдерживаемая ссылка уведомления."
                return False
            fields = [str(title)[:256]]
            if subtitle:
                fields.append(str(subtitle)[:256])
            fields.append(str(body)[:1000])
            generation = self.generation
            toast = self._toast_type(text_fields=fields, audio=self._audio_type(silent=bool(silent)),
                launch_action=url or None,
                on_activated=(lambda _args: on_activate()) if on_activate and not url else None,
                on_failed=lambda args: self._toast_failed(generation, args))
            self.error = ""
            self._toaster.show_toast(toast)
            self._retained.append(toast)
            # Accepted by Windows; Focus/notification settings still determine banners.
            return not bool(self.error)  # Preserve a Failed event raised inside Show.
        except Exception as error:
            self.error = _safe_error("Не удалось сформировать или отправить уведомление TGFocus", error)
            return False

    def _toast_failed(self, generation, args):
        if generation != self.generation or not self.available:
            return
        try:
            code = args.error_code
            error = OSError()
            if isinstance(code, int) and not isinstance(code, bool):
                error.winerror = code
            self.error = _safe_error("Windows отклонила уведомление после отправки", error)
        except Exception as error:
            self.error = _safe_error("Не удалось прочитать причину отказа уведомления", error)
        if self.on_failure:
            try:
                self.on_failure(generation, self.error)
            except Exception:
                pass  # The GUI may already be going away; never escape a native callback.

    def clear(self):
        if self._toaster:
            try:
                self._toaster.clear_toasts()
            except Exception:
                self.error = "Не удалось очистить уведомления TGFocus."
        self._retained.clear()

    def close(self):
        self.generation += 1
        self._retained.clear()
        self._toaster = None
        self.available = False
