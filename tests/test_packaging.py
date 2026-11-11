"""Frozen/source command parity without invoking a real installed program."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from hamrahi_backup import runtime
from hamrahi_backup.engine import BackupError, BackupManager
from hamrahi_backup.startup import StartupRegistration, StartupPathError
from test_engine import FakeCloud


class PackagingTests(unittest.TestCase):
    def test_overlong_startup_has_actionable_error_and_preserves_config(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = BackupManager(Path(directory), FakeCloud())
            before = manager.config_path.read_bytes() if manager.config_path.exists() else None
            try:
                with patch.object(manager.startup, 'read', return_value=None), patch.object(manager.startup, 'configure', side_effect=StartupPathError()):
                    with self.assertRaisesRegex(BackupError, '۲۶۰'):
                        manager.save_config(manager.config | {'startupEnabled': True})
                self.assertFalse(manager.config['startupEnabled'])
                self.assertEqual(manager.config_path.read_bytes() if manager.config_path.exists() else None, before)
            finally:
                manager.close()

    def test_frozen_commands_use_self_executable_and_absolute_private_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            exe = root / 'نصب برنامه' / 'AbreHamrahiAPI.exe'
            state = root / 'حساب مستقل'
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(exe)):
                worker = runtime.command('serve', state, 8877)
                self.assertEqual(worker[:2], [str(exe), '--serve'])
                self.assertEqual(worker[2:], ['--data-dir', str(state), '--port', '8877', '--no-browser'])
                self.assertNotIn('.py', StartupRegistration(state, 8877).command())
                self.assertEqual(runtime.command('desktop', state, 8877)[0], str(exe))

    def test_source_runner_is_absolute_and_unknown_role_rejected(self):
        parts = runtime.command('serve', Path('example'), 8765)
        self.assertTrue(Path(parts[0]).is_absolute())
        self.assertTrue(Path(parts[1]).is_file())
        self.assertIn('startup_runner.py', parts[1])
        with self.assertRaises(ValueError):
            runtime.command('arbitrary-script', Path('example'), 8765)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows folder picker dispatch')
    def test_frozen_picker_uses_private_result_file_without_python_or_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = BackupManager(Path(directory), FakeCloud())
            def choose(args, **kwargs):
                self.assertEqual(args[1], '--folder-picker')
                output = Path(args[2])
                self.assertTrue(output.is_relative_to(manager.state_dir))
                output.write_text('{"path":"","cancelled":true}', encoding='utf-8')
                return Mock(returncode=0, stdout=b'')
            try:
                with patch.object(sys, 'frozen', True, create=True), patch('hamrahi_backup.engine.subprocess.run', side_effect=choose):
                    self.assertEqual(manager.browse(), {'path': '', 'cancelled': True})
                self.assertEqual(list(manager.state_dir.glob('picker-*')), [])
            finally:
                manager.close()
