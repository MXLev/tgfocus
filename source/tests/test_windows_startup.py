import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Windows"))
import startup


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "Startup"
        self.executable = self.root / "Program's Folder" / "TGFocus.exe"
        self.executable.parent.mkdir()
        self.executable.write_bytes(b"test placeholder; never executed")
        self.link = self.directory / "TGFocus.lnk"

        def write(path, executable, *, arguments, app_id):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(dict(executable=str(executable), arguments=arguments, app_id=app_id)))

        self.write = Mock(side_effect=write)
        for target, value in [
            ("startup._launcher", Mock(return_value=(self.executable, ""))),
            ("startup.create_shortcut", self.write),
            ("startup.read_shortcut", Mock(side_effect=lambda path: json.loads(path.read_text()))),
        ]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.service = startup.StartupService(directory=self.directory)

    def test_read_only_initialization_and_refresh_never_register_startup(self):
        self.service.refresh()
        self.assertFalse(self.directory.exists())
        self.assertFalse(self.service.registered)
        self.write.assert_not_called()

    def test_explicit_enable_is_idempotent_and_disable_removes_only_own_shortcut(self):
        self.assertTrue(self.service.set_enabled(True))
        self.assertEqual(json.loads(self.link.read_text()), dict(executable=str(self.executable),
                         arguments="--autostart", app_id=startup.APP_ID))
        self.assertTrue(self.service.registered and self.service.current)
        self.assertTrue(self.service.set_enabled(True))
        self.write.assert_called_once()
        other = self.directory / "Other.lnk"
        other.write_bytes(b"unrelated")
        self.assertTrue(self.service.set_enabled(False))
        self.assertFalse(self.link.exists())
        self.assertEqual(other.read_bytes(), b"unrelated")
        self.service.refresh()
        self.assertFalse(self.link.exists())  # Never restore an externally removed entry.
        self.assertTrue(self.service.set_enabled(False))

    def test_foreign_and_unreadable_shortcuts_are_never_overwritten_or_removed(self):
        self.directory.mkdir()
        for data in [json.dumps(dict(executable="other.exe", arguments="", app_id="Other.App")), "invalid link"]:
            self.link.write_text(data)
            for enabled in [True, False]:
                self.assertFalse(self.service.set_enabled(enabled))
                self.assertEqual(self.link.read_text(), data)
        self.write.assert_not_called()

    def test_moved_copy_requires_an_explicit_update(self):
        self.assertTrue(self.service.set_enabled(True))
        existing = json.loads(self.link.read_text())
        existing["executable"] = str(self.root / "old-copy.exe")
        self.link.write_text(json.dumps(existing))
        self.service.refresh()
        self.assertTrue(self.service.registered)
        self.assertFalse(self.service.current)
        self.assertEqual(json.loads(self.link.read_text()), existing)
        self.assertTrue(self.service.set_enabled(True))
        self.assertTrue(self.service.current)
        self.assertEqual(self.write.call_count, 2)

    def test_source_launcher_preserves_quoted_script_and_adds_only_startup_flag(self):
        script = '"C:\\Some Folder\\app.py"'
        with patch("startup._launcher", return_value=(self.executable, script)):
            self.assertTrue(self.service.set_enabled(True))
            self.assertEqual(json.loads(self.link.read_text())["arguments"], script + " --autostart")

    def test_failed_create_does_not_report_success_or_leak_exception_text(self):
        self.write.side_effect = OSError("private path or other OS detail")
        self.assertFalse(self.service.set_enabled(True))
        self.assertFalse(self.service.registered)
        self.assertNotIn("private", self.service.status_text)


if __name__ == "__main__":
    unittest.main()
