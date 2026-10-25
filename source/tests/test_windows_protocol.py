import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Windows"))
from protocol import AIFailureGate, CommandQueue, LineDecoder


class ProtocolTests(unittest.TestCase):
    def test_utf8_split_invalid_and_oversized_frames_recover(self):
        decoder = LineDecoder(maximum=64)
        objects = []
        for byte in '{"text":"Срочно 📨"}\n'.encode():
            objects.extend(decoder.consume(bytes([byte])))
        self.assertEqual(objects, [{"text": "Срочно 📨"}])
        self.assertEqual(decoder.consume(b'not json\n[1,2]\n'), [])
        self.assertEqual(decoder.consume(b'x' * 65), [])
        self.assertEqual(decoder.consume(b'\n{"ready":true}\n'), [{"ready": True}])
        self.assertEqual(decoder.consume(b'{"next":true}\n{"partial"'), [{"next": True}])
        self.assertEqual(decoder.consume(b':1}\n'), [{"partial": 1}])

    def test_config_updates_coalesce_and_precede_bounded_commands(self):
        queue = CommandQueue(maximum=1)
        self.assertTrue(queue.put({"command": "connect", "apiHash": "SECRET"}))
        self.assertFalse(queue.put({"command": "phone"}))
        self.assertTrue(queue.put({"command": "config", "enabled": True}))
        self.assertTrue(queue.put({"command": "config", "enabled": False}))
        self.assertFalse(json.loads(queue.get())["enabled"])
        self.assertEqual(json.loads(queue.get())["command"], "connect")
        queue.close()
        self.assertIsNone(queue.get())
        self.assertFalse(queue.put({"command": "config"}))

    def test_close_wakes_blocked_writer_and_drops_secret_references(self):
        queue = CommandQueue()
        values = []
        thread = threading.Thread(target=lambda: values.append(queue.get()), daemon=True)
        thread.start()
        queue.close()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(values, [None])
        self.assertEqual(len(queue.commands), 0)
        self.assertIsNone(queue.config)

    def test_ai_outage_notification_is_suppressed_until_recovery_and_cooldown(self):
        gate = AIFailureGate()
        self.assertFalse(gate.should_notify("unchanged", now=0))
        self.assertTrue(gate.should_notify("failed", now=0))
        self.assertFalse(gate.should_notify("failed", now=600))
        self.assertFalse(gate.should_notify("ready", now=601))
        self.assertTrue(gate.should_notify("failed", now=602))
        self.assertFalse(gate.should_notify("ready", now=603))
        self.assertFalse(gate.should_notify("failed", now=604))
        self.assertTrue(gate.should_notify("failed", now=902))


if __name__ == "__main__": unittest.main()
