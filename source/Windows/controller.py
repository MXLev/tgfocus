"""Tray-client state. Credentials cross only DPAPI and the worker's stdin pipe."""
import copy
import re
import sys
from collections import deque
from datetime import datetime

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Qt, Signal, Slot

from bridge import WorkerBridge
from platform_services import NotificationService
from dnd import WnfDndMonitor, is_resume_message
from protocol import AIFailureGate
from storage import ConfigStore, CredentialVault, DEFAULT_CONFIG, normalize_config


class _ResumeFilter(QAbstractNativeEventFilter):
    def __init__(self, wake):
        super().__init__()
        self.wake = wake

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG") and is_resume_message(message):
            self.wake()
        return False, 0


class Controller(QObject):
    changed = Signal()
    qr_changed = Signal(str)
    simulation_received = Signal(bool, str)
    settings_requested = Signal(str)
    _dnd_dirty = Signal()

    def __init__(self, directory, *, smoke=False, parent=None):
        super().__init__(parent)
        self.config_store = ConfigStore(directory)
        self.vault = CredentialVault(directory)
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self._secrets = {}
        self.state, self.status_text = "disconnected", "Аккаунт ещё не подключён"
        self.account_name = self.error_text = self.qr_url = ""
        self.ai_status = "ИИ выключен"
        self.dialogs = []
        self.activities = deque(maxlen=50)
        self.passed = self.suppressed = 0
        self.helper_ready = False
        self.focus_state = None
        self.focus_status = "Ручное управление фильтром"
        self.can_resend = False
        self.resend_at = 0
        self.qr_expires_at = 0
        self.delivery_method = ""
        self._smoke = smoke
        self._closing = False
        self._ai_failure = AIFailureGate()
        self.notifications = None if smoke else NotificationService()
        if not smoke:
            try:
                self.config = self.config_store.load()
            except Exception:
                self.error_text = "Не удалось прочитать настройки. Фильтр выключен; исходный файл не перезаписан."
            try:
                self._secrets = self.vault.load()
            except Exception:
                self.error_text = "Не удалось прочитать защищённые данные. Войди заново; старый файл не изменён."
        self._dnd_dirty.connect(self._dnd_changed, Qt.ConnectionType.QueuedConnection)
        self.dnd = WnfDndMonitor(self._dnd_dirty.emit)
        self._resume_filter = _ResumeFilter(self._dnd_dirty.emit)
        if sys.platform == "win32" and not smoke:
            QCoreApplication.instance().installNativeEventFilter(self._resume_filter)
        self.bridge = WorkerBridge(self)
        self.bridge.event.connect(self.receive)
        self.bridge.error.connect(self._worker_error)
        self.bridge.stopped.connect(self._worker_stopped)
        self._configure_focus()

    @property
    def effective_filter_enabled(self):
        return self.config.get("enabled", False) and (
            not self.config.get("windowsFocusAutomation", False) or self.focus_state is True)

    @property
    def filter_title(self):
        return "Фильтр включён" if self.effective_filter_enabled else "Фильтр выключен"

    @property
    def connected(self):
        return self.state == "connected"

    def start(self):
        self.bridge.start()

    @Slot(str)
    def _worker_error(self, message):
        self.error_text = message
        self.changed.emit()

    @Slot()
    def _worker_stopped(self):
        self.helper_ready = False
        self.state = "disconnected"
        self.status_text = "Модуль Telegram остановлен; перезапусти приложение"
        self.changed.emit()

    def clear_error(self):
        self.error_text = ""
        self.changed.emit()

    def update_config(self, changes):
        try:
            updated = normalize_config(dict(self.config, **changes))
        except Exception:
            self.error_text = "Настройки не применены: проверь значения полей."
            self.changed.emit()
            return False
        self.config = updated
        if not self.config.get("aiEnabled"):
            self._ai_failure.reset()
        if not self._smoke:
            try:
                self.config_store.save(self.config)
            except Exception:
                self.error_text = "Настройки работают до перезапуска, но не сохранены на диск."
        self._configure_focus()
        self._sync_config()  # Privacy changes apply even when writing preferences failed.
        self.changed.emit()
        return True

    def set_filter_enabled(self, enabled):
        self.update_config({"enabled": bool(enabled)})

    def _configure_focus(self):
        if self.config.get("enabled") and self.config.get("windowsFocusAutomation"):
            if not self._smoke:
                self.dnd.start()
            self._refresh_focus()
        else:
            self.dnd.close()
            self.focus_state = None
            self.focus_status = ("Авто-режим: главный фильтр выключен" if self.config.get("windowsFocusAutomation")
                                 else "Ручное управление фильтром")

    @Slot()
    def _dnd_changed(self):
        if not self._closing and self.config.get("enabled") and self.config.get("windowsFocusAutomation"):
            self._refresh_focus()

    def _refresh_focus(self):
        if self._smoke:
            self.focus_state, self.focus_status = None, "Проверка без доступа к пользовательскому состоянию"
        else:
            self.focus_state, self.focus_status = self.dnd.read()
        self._sync_config()
        self.changed.emit()

    def _sync_config(self):
        if not self.helper_ready or self._closing:
            return
        config = copy.deepcopy(self.config)
        config["processingEnabled"] = self.effective_filter_enabled
        config["aiApiKey"] = self._secrets.get("aiKeys", {}).get(config.get("aiProvider"), "") if config.get("aiEnabled") else ""
        self.bridge.send({"command": "config", "config": config})

    def _save_secrets(self):
        if self._smoke:
            self.error_text = "В проверочном режиме вход и сохранение ключей отключены."
            self.changed.emit()
            return False
        try:
            self.vault.save(self._secrets)
            return True
        except Exception:
            self.error_text = "Не удалось сохранить данные в защищённое хранилище Windows."
            self.changed.emit()
            return False

    def connect_telegram(self, api_id, api_hash):
        api_id, api_hash = str(api_id).strip(), str(api_hash).strip()
        if not re.fullmatch(r"[0-9]{1,10}", api_id) or not 0 < int(api_id) < 2**31 or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
            self._worker_error("Введи API ID числом и API Hash из 32 шестнадцатеричных символов с my.telegram.org.")
            return
        if not self.helper_ready:
            self._worker_error("Подожди запуска модуля Telegram.")
            return
        previous = copy.deepcopy(self._secrets)
        self._secrets.update(apiId=api_id, apiHash=api_hash)
        if not self._save_secrets():
            self._secrets = previous
            return
        self.error_text = ""
        self._connect_saved()

    def _connect_saved(self):
        self.bridge.send({"command": "connect", "apiId": self._secrets.get("apiId", ""),
            "apiHash": self._secrets.get("apiHash", ""), "session": self._secrets.get("session", "")})

    def auth(self, command, value=None):
        if self._smoke:
            return
        if command not in {"phone", "code", "password", "qr", "resend", "cancelAuth"}:
            return
        request = {"command": command}
        if command in {"phone", "code", "password"}:
            if not isinstance(value, str) or not value or len(value) > 4096:
                return
            request[command] = value
        self.error_text = ""
        self.bridge.send(request)
        self.changed.emit()

    def load_dialogs(self):
        if not self.connected:
            self._worker_error("Сначала подключи Telegram во вкладке «Подключение».")
            return
        self.bridge.send({"command": "dialogs"})

    def simulate(self, message):
        if self.helper_ready:
            self.bridge.send({"command": "simulate", "message": message})

    def has_ai_key(self, provider=None):
        return bool(self._secrets.get("aiKeys", {}).get(provider or self.config.get("aiProvider")))

    def save_ai_key(self, key):
        key = key.strip()
        if len(key) > 4096 or "\n" in key or "\r" in key:
            self._worker_error("API-ключ должен быть одной строкой, не длиннее 4096 символов.")
            return
        previous = copy.deepcopy(self._secrets)
        keys = self._secrets.setdefault("aiKeys", {})
        if key:
            keys[self.config["aiProvider"]] = key
        else:
            keys.pop(self.config["aiProvider"], None)
        if not self._save_secrets():
            self._secrets = previous
            return
        self.ai_status = "Ключ сохранён" if key else "Ключ удалён"
        self._sync_config()
        self.changed.emit()

    def test_ai(self):
        if not self.config.get("aiEnabled"):
            self._worker_error("Сначала включи отправку сообщений в ИИ.")
            return
        self.ai_status = "Проверяю на вымышленном сообщении…"
        self.bridge.send({"command": "testAI"})
        self.changed.emit()

    def logout(self):
        self.set_filter_enabled(False)
        self.bridge.send({"command": "logout"})

    def test_notification(self):
        if not self._notify("TGFocus", "Проверка доставки. Во время «Не беспокоить» разреши TGFocus в приоритетных уведомлениях Windows."):
            self._worker_error("Windows не приняла уведомление. Проверь настройки уведомлений TGFocus.")

    def _notify(self, title, body, *, subtitle="", silent=False, url="", on_activate=None):
        return self.notifications is not None and self.notifications.show(title, body, subtitle=subtitle,
            silent=silent, url=url, on_activate=on_activate)

    @Slot(dict)
    def receive(self, event):
        kind = event.get("type")
        if kind == "ready":
            self.helper_ready = True
            self._sync_config()
            if self._secrets.get("session") and not self._smoke:
                self._connect_saved()
        elif kind == "state":
            self.state = event.get("state", "disconnected")
            self.status_text = event.get("message", "")
            if event.get("name"):
                self.account_name = event["name"]
            if self.state != "qr":
                self.qr_url = ""
                self.qr_changed.emit("")
        elif kind == "qr":
            url = event.get("url", "")
            self.qr_url = url if re.fullmatch(r"tg://login\?token=[A-Za-z0-9_-]{1,2048}", url) else ""
            self.qr_expires_at = event.get("expiresAt", 0)
            self.qr_changed.emit(self.qr_url)
        elif kind == "delivery":
            self.can_resend = bool(event.get("canResend"))
            self.resend_at = event.get("resendAt", 0)
            self.delivery_method = event.get("method", "")
        elif kind == "session":
            self._secrets["session"] = event.get("session", "")
            self._save_secrets()
        elif kind == "error":
            self.error_text = event.get("message", "Ошибка модуля Telegram")
            if not event.get("recoverable"):
                self.state = "disconnected"
        elif kind == "invalidSession":
            self._secrets["session"] = ""
            self._save_secrets()
            self.state, self.status_text = "disconnected", "Сессия отозвана. Войди заново."
        elif kind == "loggedOut":
            for key in ("apiId", "apiHash", "session"):
                self._secrets.pop(key, None)
            self._save_secrets()
            self.dialogs, self.account_name = [], ""
            self.state, self.status_text = "disconnected", "Выход выполнен. Сессия Telegram отозвана."
        elif kind == "dialogs":
            self.dialogs = event.get("items", [])[:500]
        elif kind == "simulation":
            self.simulation_received.emit(bool(event.get("allowed")), event.get("reason", ""))
        elif kind == "aiStatus" and self.config.get("aiEnabled"):
            self.ai_status = event.get("message", "")
            if self._ai_failure.should_notify(event.get("connection")):
                self._notify("Подключение к ИИ не работает",
                    "ИИ-фильтр временно недоступен. Применяются локальные правила. Проверь настройки ИИ.",
                    silent=True, on_activate=lambda: self.settings_requested.emit("ai"))
        elif kind == "message" and self.effective_filter_enabled:
            self._message(event)
        self.changed.emit()

    def _message(self, event):
        allowed = bool(event.get("allowed"))
        if allowed and self.config.get("windowsFocusAutomation"):
            # Recheck the current native state even if a queued WNF signal is
            # delayed; an old AI/worker result must not deliver after DND ends.
            self._refresh_focus()
            if not self.effective_filter_enabled:
                return
        self.passed += int(allowed)
        self.suppressed += int(not allowed)
        self.activities.appendleft({"time": datetime.now().strftime("%H:%M:%S"), "title": event.get("title", "Telegram"),
            "reason": event.get("reason", ""), "allowed": allowed, "url": event.get("url", "tg://")})
        if allowed:
            body = event.get("text", "Новое сообщение") if self.config.get("showPreview") else "Новое важное сообщение в Telegram"
            if not self._notify(event.get("title", "Telegram"), body,
                subtitle=event.get("sender", "") if event.get("kind") != "private" else "",
                silent=not self.config.get("playSound"), url=event.get("url", "tg://")):
                self.error_text = "Windows не приняла уведомление. Проверь настройки уведомлений TGFocus."

    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        self.dnd.close()
        if sys.platform == "win32" and not self._smoke:
            QCoreApplication.instance().removeNativeEventFilter(self._resume_filter)
        self.bridge.stop()
        if self.notifications:
            self.notifications.close()
