import asyncio
import io
import json
import queue
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Helper"))
from worker import Worker


class WorkerInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_and_exclusion_bypass_busy_commands_and_full_queue(self):
        worker = Worker()
        worker.config = {"enabled": True, "aiEnabled": True}
        lines = queue.Queue()
        started, configured, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = worker.command

        async def command(data):
            if data.get("command") == "dialogs":
                started.set()
                try:
                    await asyncio.Event().wait()  # A slow Telegram API operation.
                finally:
                    cancelled.set()
            result = await original(data)
            if data.get("command") == "config":
                configured.set()
            return result

        class Input:
            def readline(self, size):
                return lines.get(timeout=3)

        worker.command = command
        output = io.StringIO()
        with patch("worker.sys.stdin", Input()), redirect_stdout(output):
            task = asyncio.create_task(worker.run())
            try:
                lines.put(json.dumps({"command": "dialogs"}) + "\n")
                await asyncio.wait_for(started.wait(), timeout=2)
                for _ in range(30):
                    lines.put(json.dumps({"command": "dialogs"}) + "\n")
                lines.put(json.dumps({"command": "config", "config": {"enabled": False,
                    "aiEnabled": False, "aiExcludedChats": [{"id": "42"}]}}) + "\n")
                await asyncio.wait_for(configured.wait(), timeout=2)
                self.assertFalse(worker.processing_enabled())
                self.assertFalse(worker.config["aiEnabled"])
                self.assertEqual(worker.config["aiExcludedChats"], [{"id": "42"}])
                self.assertFalse(cancelled.is_set())  # Slow command still hasn't returned.
                self.assertIn("Слишком много команд", output.getvalue())
            finally:
                lines.put('{"command":"quit"}\n')
                await asyncio.wait_for(task, timeout=2)
            self.assertTrue(cancelled.is_set())  # Quit also bypasses the blocked queue.


if __name__ == "__main__": unittest.main()
