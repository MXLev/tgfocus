"""Experimental personal Windows client: a tray icon, settings, bundled worker."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback

from PySide6.QtCore import QLockFile, QPointF, QSignalBlocker, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from controller import Controller
from platform_services import NotificationService, read_focus_state, _ensure_winrt
from storage import CredentialVault
from window import SettingsWindow


def tray_icon(enabled):
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#2779e8" if enabled else "#777777"))
    painter.setBrush(QColor("#2779e8" if enabled else "#777777"))
    painter.drawEllipse(1, 1, 30, 30)
    path = QPainterPath(QPointF(7, 14))
    path.lineTo(25, 7)
    path.lineTo(19, 25)
    path.lineTo(14, 18)
    path.closeSubpath()
    painter.setPen(QColor("white"))
    painter.setBrush(QColor("white"))
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


def data_directory():
    override = os.environ.get("TELEGRAM_FOCUS_DATA_DIR")
    if override:
        return Path(override)
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        raise RuntimeError("Windows LocalAppData is unavailable")
    return Path(root) / "TGFocus"


def smoke_native(directory):
    if sys.platform != "win32":
        return {"platform": "non-Windows UI/IPC test", "native": "not tested"}
    # Import explicitly: unavailable server features are different from missing
    # frozen Python bindings, which must fail the packaging check.
    import winrt.windows.ui.shell
    import winrt.windows.ui.notifications
    import windows_toasts
    _ensure_winrt()  # Core runtime initialization must work even on a CI server.
    vault = CredentialVault(directory / "native-smoke")
    fake = {"apiId": "1", "apiHash": "TEST_HASH", "session": "TEST_SESSION",
            "aiKeys": {"openrouter": "TEST_KEY_NOT_REAL"}}
    vault.save(fake)
    if vault.load() != fake:
        raise RuntimeError("DPAPI round trip failed")
    ciphertext = vault.path.read_bytes()
    if b"TEST_SESSION" in ciphertext or b"TEST_KEY_NOT_REAL" in ciphertext:
        raise RuntimeError("Plaintext credentials in DPAPI vault")
    active, focus_status = read_focus_state()
    notifications = NotificationService()
    available = notifications.available
    notifications.close()  # No actual notifications or account/network requests.
    return {"platform": "Windows", "dpapi": "OK", "focus": active,
            "focus_status": focus_status, "native_notifications_available": available}


def write_receipt(value):
    destination = os.environ.get("TELEGRAM_FOCUS_SMOKE_RESULT")
    if destination:
        Path(destination).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    if sys.stdout is not None:
        print("Windows app startup smoke test: " + ("OK" if value.get("ok") else "FAILED"), flush=True)


def main():
    smoke = "--startup-smoke-test" in sys.argv
    if sys.platform != "win32" and not smoke:
        if sys.stderr is not None:
            print("This tray client is for Windows. Use the native macOS app on a Mac.", file=sys.stderr)
        return 2
    temporary = None
    if smoke and not os.environ.get("TELEGRAM_FOCUS_DATA_DIR"):
        temporary = tempfile.TemporaryDirectory(prefix="tgfocus-ui-smoke-")
        os.environ["TELEGRAM_FOCUS_DATA_DIR"] = temporary.name
    application = QApplication(sys.argv)
    application.setApplicationName("TGFocus")
    application.setOrganizationName("MXLev")
    application.setQuitOnLastWindowClosed(False)
    application.setWindowIcon(tray_icon(False))
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(directory / "runtime.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        if not smoke:
            QMessageBox.information(None, "TGFocus уже запущен", "Открой значок TGFocus рядом с часами или под стрелкой ^. Для другой версии сначала заверши текущую через меню значка.")
        return 1
    controller = Controller(directory, smoke=smoke)
    window = SettingsWindow(controller)
    controller.settings_requested.connect(window.show_page)
    application.aboutToQuit.connect(controller.shutdown)

    if smoke:
        deadline = time.monotonic() + 20
        timer = QTimer()
        timer.setInterval(50)

        def check():
            if not controller.helper_ready:
                if time.monotonic() >= deadline:
                    timer.stop()
                    write_receipt({"ok": False, "error": "WorkerStartupTimeout"})
                    application.exit(1)
                return
            timer.stop()
            try:
                window.show_page("overview")
                if not window.isVisible():
                    raise RuntimeError("Settings did not open")
                window.close()
                if window.isVisible():
                    raise RuntimeError("Settings did not hide")
                window.show_page("ai")
                if not window.isVisible():
                    raise RuntimeError("Settings did not reopen")
                window.hide()
                result = smoke_native(directory)
                write_receipt({"ok": True, "checks": result})
                application.exit(0)
            except Exception as error:
                # Smoke uses isolated, synthetic state and no account secrets.
                write_receipt({"ok": False, "error": traceback.format_exc()})
                application.exit(1)
        timer.timeout.connect(check)
        timer.start()
    else:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            QMessageBox.critical(None, "Системный трей недоступен", "TGFocus нужен системный трей Windows рядом с часами. Запусти приложение в обычном сеансе рабочего стола.")
            return 1
        tray = QSystemTrayIcon(tray_icon(False), application)
        menu = QMenu()
        status = QAction(controller.filter_title, menu)
        status.setEnabled(False)
        menu.addAction(status)
        master = QAction("Разрешить работу фильтра", menu)
        master.setCheckable(True)
        master.toggled.connect(controller.set_filter_enabled)
        menu.addAction(master)
        connection = QAction(controller.status_text, menu)
        connection.setEnabled(False)
        menu.addAction(connection)
        menu.addSeparator()
        settings = menu.addAction("Открыть настройки…")
        settings.triggered.connect(lambda: window.show_page("overview"))
        test = menu.addAction("Тестовое уведомление")
        test.triggered.connect(controller.test_notification)
        menu.addSeparator()
        menu.addAction("Завершить TGFocus", application.quit)
        tray.setContextMenu(menu)

        def refresh():
            status.setText(controller.filter_title)
            connection.setText(controller.status_text)
            blocker = QSignalBlocker(master)
            master.setChecked(controller.config.get("enabled", False))
            del blocker
            tray.setIcon(tray_icon(controller.effective_filter_enabled))
            tray.setToolTip("TGFocus — " + controller.filter_title + "\n" + controller.status_text)

        controller.changed.connect(refresh)
        tray.activated.connect(lambda reason: window.show_page("overview") if reason in (
            QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        application.aboutToQuit.connect(tray.hide)
        tray.show()
        refresh()
        if not controller._secrets.get("session"):
            window.show_page("connection")
    QTimer.singleShot(0, controller.start)
    result = application.exec()
    controller.shutdown()
    lock.unlock()
    if temporary:
        temporary.cleanup()
    return result


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        if "--startup-smoke-test" in sys.argv:
            write_receipt({"ok": False, "error": traceback.format_exc()})
        elif sys.stderr is not None:
            print("TGFocus не удалось запустить: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)
