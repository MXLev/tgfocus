from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_support import app_update_lock, ensure_app_stopped, install_app


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = self.root / "Telegram Focus.app"
        self.staged = self.root / "staged.app"
        for app, text in [(self.app, "previous"), (self.staged, "replacement")]:
            app.mkdir()
            (app / "version").write_text(text)
        self.lock_directory = self.root / "data"
        self.stopped = patch('build_support.ensure_app_stopped')
        self.stopped_mock = self.stopped.start()
        self.addCleanup(self.stopped.stop)

    def install(self, validate=lambda candidate: None):
        install_app(self.staged, self.app, validate, self.lock_directory)

    def assert_previous_intact(self):
        self.assertEqual((self.app / "version").read_text(), "previous")

    def test_replacement_preserves_bundle_symlinks(self):
        (self.staged / "alias").symlink_to("version")
        self.install(lambda candidate: self.assertEqual((candidate / "version").read_text(), "replacement"))
        self.assertEqual((self.app / "version").read_text(), "replacement")
        self.assertTrue((self.app / "alias").is_symlink())
        self.assertFalse(list(self.root.glob('.tgfocus-install-*')))

    def test_copy_failure_preserves_previous_app(self):
        with patch('build_support.shutil.copytree', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.install()
        self.assert_previous_intact()

    def test_signature_failure_preserves_previous_app(self):
        def reject(candidate):
            raise RuntimeError('signature verification failed')
        with self.assertRaises(RuntimeError):
            self.install(reject)
        self.assert_previous_intact()

    def test_failed_swap_restores_previous_app(self):
        rename = Path.rename
        def fail_replacement(path, destination):
            if path.name == self.app.name and path.parent != self.root:
                raise OSError('replacement failed')
            return rename(path, destination)
        with patch.object(Path, 'rename', fail_replacement):
            with self.assertRaises(OSError):
                self.install()
        self.assert_previous_intact()

    def test_failed_rollback_keeps_recovery_copy(self):
        rename = Path.rename
        def fail_after_backup(path, destination):
            if path.parent != self.root:
                raise OSError('rename failed')
            return rename(path, destination)
        with patch.object(Path, 'rename', fail_after_backup):
            with self.assertRaises(OSError):
                self.install()
        copies = list(self.root.glob('.tgfocus-install-*/previous.app/version'))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_text(), "previous")

    def test_running_app_blocks_replacement(self):
        self.stopped_mock.side_effect = RuntimeError('app is running')
        with self.assertRaises(RuntimeError):
            self.install()
        self.assert_previous_intact()

    def test_runtime_lock_blocks_replacement_and_releases_after_exit(self):
        with app_update_lock(self.lock_directory):
            with self.assertRaises(RuntimeError):
                self.install()
            self.assert_previous_intact()
        self.install()
        self.assertEqual((self.app / "version").read_text(), "replacement")

    def test_only_exact_executable_path_is_blocked(self):
        self.stopped.stop()
        executable = self.app / 'Contents/MacOS/TelegramFocus'
        for command, blocked in [(str(executable), True), ('/other/TelegramFocus', False),
                                 (str(executable) + '-test', False)]:
            result = subprocess.CompletedProcess([], 0, stdout=command + '\n')
            with self.subTest(command=command), patch('build_support.subprocess.run', return_value=result):
                if blocked:
                    with self.assertRaises(RuntimeError):
                        ensure_app_stopped(self.app)
                else:
                    ensure_app_stopped(self.app)
