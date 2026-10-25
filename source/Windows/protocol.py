"""Small bounded protocol helpers shared by the tray client and its tests."""
import json
import threading
import time
from collections import deque


class LineDecoder:
    def __init__(self, maximum=1_048_576):
        self.maximum = maximum
        self.pending = bytearray()
        self.discarding = False

    def consume(self, data):
        objects = []
        for index, fragment in enumerate(data.split(b"\n")):
            if index:
                if not self.discarding:
                    try:
                        value = json.loads(self.pending)
                        if isinstance(value, dict):
                            objects.append(value)
                    except (ValueError, UnicodeError):
                        pass
                self.pending.clear()
                self.discarding = False
            if not self.discarding:
                if len(self.pending) + len(fragment) <= self.maximum:
                    self.pending.extend(fragment)
                else:
                    self.pending.clear()
                    self.discarding = True
        return objects


class CommandQueue:
    """One latest config plus a bounded FIFO. No secrets/messages in diagnostics."""
    def __init__(self, maximum=16):
        self.maximum = maximum
        self.commands = deque()
        self.config = None
        self.closed = False
        self.condition = threading.Condition()

    def put(self, command):
        data = json.dumps(command, ensure_ascii=False).encode("utf-8") + b"\n"
        if len(data) > 1_048_576:
            return False
        with self.condition:
            if self.closed:
                return False
            if command.get("command") == "config":
                self.config = data
            else:
                if len(self.commands) >= self.maximum:
                    return False
                self.commands.append(data)
            self.condition.notify()
            return True

    def get(self):
        with self.condition:
            self.condition.wait_for(lambda: self.closed or self.config is not None or self.commands)
            if self.closed:
                return None
            if self.config is not None:
                result, self.config = self.config, None
                return result
            return self.commands.popleft()

    def close(self):
        with self.condition:
            self.closed = True
            self.config = None
            self.commands.clear()
            self.condition.notify_all()


class AIFailureGate:
    def __init__(self):
        self.outage = False
        self.last_notice = None

    def reset(self):
        self.outage = False

    def should_notify(self, connection, now=None):
        now = time.monotonic() if now is None else now
        if connection == "ready":
            self.outage = False
        if connection != "failed" or self.outage:
            return False
        if self.last_notice is not None and now - self.last_notice < 300:
            return False
        self.outage = True
        self.last_notice = now
        return True
