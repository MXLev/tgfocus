"""Nonblocking, bounded stdin/stdout IPC; the GUI never waits on pipe writes."""
import os
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from protocol import CommandQueue, LineDecoder


class WorkerBridge(QObject):
    event = Signal(dict)
    error = Signal(str)
    stopped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.queue = CommandQueue()
        self.closing = False
        self.reader = self.writer = None

    def start(self):
        if self.process is not None:
            return
        if getattr(sys, "frozen", False):
            command = [str(Path(sys.executable).parent / "helper" / "focus-worker.exe")]
        else:
            command = [sys.executable, "-u", str(Path(__file__).resolve().parents[1] / "Helper" / "worker.py")]
        environment = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0, env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        except OSError:
            self.error.emit("Не удалось запустить модуль Telegram. Сохрани все файлы рядом с TGFocus.exe.")
            return
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.writer = threading.Thread(target=self._write, daemon=True)
        self.reader.start()
        self.writer.start()

    def send(self, command):
        if self.closing or self.process is None or self.process.poll() is not None:
            self.error.emit("Модуль Telegram остановлен. Перезапусти TGFocus.")
            return False
        if not self.queue.put(command):
            # A lost privacy/pause update must never leave an old filter running.
            self.error.emit("Модуль перегружен. Работа остановлена; перезапусти TGFocus.")
            self.stop()
            return False
        return True

    def _read(self):
        decoder = LineDecoder()
        try:
            while data := self.process.stdout.read(65536):
                for event in decoder.consume(data):
                    self.event.emit(event)
        except (OSError, ValueError):
            pass
        finally:
            self.queue.close()
            if not self.closing:
                self.stopped.emit()

    def _write(self):
        try:
            while (data := self.queue.get()) is not None:
                # Raw Windows pipes can perform a partial write.
                view = memoryview(data)
                while view:
                    written = self.process.stdin.write(view)
                    if not written:
                        raise BrokenPipeError
                    view = view[written:]
                self.process.stdin.flush()
        except (OSError, ValueError):
            if not self.closing:
                self.error.emit("Связь с модулем Telegram прервана. Перезапусти TGFocus.")

    def stop(self):
        if self.closing:
            return
        self.closing = True
        process = self.process
        if process is not None:
            if process.poll() is None:
                self.queue.put({"command": "quit"})
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
            self.queue.close()
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
            for thread in (self.reader, self.writer):
                if thread and thread is not threading.current_thread():
                    thread.join(timeout=0.2)
