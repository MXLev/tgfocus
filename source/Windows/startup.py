"""Explicit opt-in registration in the current user's Windows Startup folder.

Read-only at construction/refresh. Never recreate a missing entry, rewrite an
existing entry on launch, or change Windows' StartupApproved settings.
"""
import sys
from pathlib import Path

from platform_services import APP_ID, _launcher
from shell_identity import create_shortcut, get_startup_directory, read_shortcut


class StartupService:
    def __init__(self, *, directory=None):
        self._directory = Path(directory) if directory is not None else None
        self.available = sys.platform == "win32" or directory is not None
        self.registered = False
        self.current = False
        self.status_text = ""
        self.refresh()

    def _path(self):
        return (self._directory if self._directory is not None else get_startup_directory()) / "TGFocus.lnk"

    @staticmethod
    def _expected():
        executable, arguments = _launcher()
        return dict(executable=str(executable), arguments=(arguments + " --autostart").strip(), app_id=APP_ID)

    def refresh(self):
        self.registered = self.current = False
        if not self.available:
            self.status_text = "Автозагрузка доступна только на Windows."
            return
        try:
            path = self._path()
            if not path.exists():
                self.status_text = "TGFocus не добавлен в автозагрузку."
                return
            existing = read_shortcut(path)
            if existing.get("app_id") != APP_ID:
                self.status_text = "Ярлык TGFocus в автозагрузке занят другой программой; он не изменён."
                return
            self.registered = True
            self.current = existing == self._expected()
            self.status_text = ("TGFocus добавлен в автозагрузку. Проверь системный переключатель Windows: "
                                "он может быть выключен независимо от наличия ярлыка." if self.current else
                                "Автозагрузка ссылается на другую копию TGFocus. Обнови путь, если перенёс папку.")
        except Exception:
            self.status_text = "Не удалось прочитать состояние автозагрузки TGFocus."

    def set_enabled(self, enabled):
        if not self.available:
            return False
        try:
            path = self._path()
            existing = read_shortcut(path) if path.exists() else None
            if existing is not None and existing.get("app_id") != APP_ID:
                raise ValueError("Another application owns this shortcut")
            if enabled:
                expected = self._expected()
                # Idempotence preserves a Windows-side disable and avoids rewriting
                # the entry merely because settings were reopened.
                if existing != expected:
                    create_shortcut(path, Path(expected["executable"]), arguments=expected["arguments"], app_id=APP_ID)
                if read_shortcut(path) != expected:
                    raise ValueError("Startup identity did not round trip")
            elif existing is not None:
                path.unlink()
            self.refresh()
            return self.registered is bool(enabled) and (not enabled or self.current)
        except Exception:
            self.refresh()
            self.status_text = "Не удалось изменить автозагрузку TGFocus. Чужие ярлыки не изменяются."
            return False
