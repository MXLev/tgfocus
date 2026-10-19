from pathlib import Path
import importlib.util
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


class BuildLifetimeTests(unittest.TestCase):
    def test_shared_lock_covers_every_build_stage_and_final_installation(self):
        spec = importlib.util.spec_from_file_location('tgfocus_build_test', Path(__file__).resolve().parents[1] / 'build.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            (root / 'README.md').write_text('test')
            helper = root / 'helper'
            helper.mkdir()
            (helper / 'focus-worker').write_text('worker')
            swift = root / 'TelegramFocus'
            swift.write_text('app')
            python = root / 'venv/bin/python'
            python.parent.mkdir(parents=True)
            python.touch()
            lock_directory = root / 'runtime'
            commands = []

            def run(*args, **kwargs):
                # A concurrent app or build must fail to acquire the real OS lock
                # at every subprocess stage, including final signature checks.
                with self.assertRaises(RuntimeError):
                    with app_update_lock(lock_directory):
                        pass
                commands.append(tuple(str(arg) for arg in args))
                if str(args[0]) == 'swiftc':
                    Path(args[args.index('-o') + 1]).write_text('binary')
                if Path(args[0]).name == 'make-icon':
                    directory = Path(args[-1])
                    directory.mkdir()
                    for size in [16, 32, 128, 256, 512]:
                        for scale in ['', '@2x']:
                            (directory / f'icon_{size}x{size}{scale}.png').write_bytes(b'PNG fixture')

            with patch.object(module, 'SOURCE', source), patch.object(module, 'PRODUCT', root), \
                 patch.object(module, 'runtime_directory', return_value=lock_directory), \
                 patch.object(module, 'run', side_effect=run), \
                 patch.object(sys, 'argv', ['build.py', '--build-dir', str(root / 'build'),
                    '--env', str(python.parents[1]), '--swift-binary', str(swift), '--helper-dir', str(helper)]):
                module.main()
            self.assertTrue((root / 'Telegram Focus.app/Contents/Info.plist').is_file())
            self.assertTrue(any(command[0] == 'codesign' for command in commands))
            self.assertTrue(any('pip' in command for command in commands))
            with app_update_lock(lock_directory):
                pass  # The complete build released its lock.
