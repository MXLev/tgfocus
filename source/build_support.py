"""Keep the installed app intact until a validated replacement is ready."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def runtime_directory():
    override = os.environ.get("TELEGRAM_FOCUS_DATA_DIR")
    return Path(override) if override else Path.home() / "Library/Application Support/TelegramFocus"


def ensure_app_stopped(app):
    executable = (app / "Contents/MacOS/TelegramFocus").resolve()
    result = subprocess.run(["ps", "-ww", "-axo", "comm="], check=True, capture_output=True, text=True)
    if any(Path(line.strip()).resolve() == executable for line in result.stdout.splitlines() if line.strip()):
        raise RuntimeError("Завершите Telegram Focus через меню приложения перед обновлением, затем повторите сборку.")


@contextmanager
def app_update_lock(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(directory / "runtime.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Telegram Focus запущен или уже обновляется. Завершите приложение и повторите сборку.") from error
        yield


def install_app(staged, app, validate, directory=None):
    """Copy/validate first; retain the previous bundle until replacement succeeds."""
    with app_update_lock(directory if directory is not None else runtime_directory()):
        ensure_app_stopped(app)  # Also detects older versions without runtime.lock.
        if app.is_symlink():
            raise RuntimeError("Путь приложения является символической ссылкой; замените приложение вручную.")
        work = Path(tempfile.mkdtemp(prefix=".tgfocus-install-", dir=app.parent))
        backup = work / "previous.app"
        try:
            replacement = Path(work) / app.name
            shutil.copytree(staged, replacement, symlinks=True)
            validate(replacement)
            ensure_app_stopped(app)
            had_previous = app.exists()
            if had_previous:
                app.rename(backup)
            try:
                replacement.rename(app)
            except OSError:
                if had_previous:
                    backup.rename(app)
                raise
        finally:
            # If rollback itself failed, retain the only copy for recovery.
            if backup.exists() and not app.exists():
                print(f"Предыдущая версия сохранена для восстановления: {backup}", file=sys.stderr)
            else:
                shutil.rmtree(work)
