"""Update policy, source confinement and interrupted download regressions."""
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import os
import threading
from hamrahi_backup import updater
from hamrahi_backup.engine import BackupManager, BackupError
from test_engine import FakeCloud

def release():
    name = 'AbreHamrahiAPI-1.8.0-windows-x64-setup.exe'
    return {'tag_name': 'v1.8.0', 'published_at': '2026-11-01T00:00:00Z', 'assets': [
        {'name': name, 'browser_download_url': f'https://github.com/{updater.REPO}/releases/download/v1.8.0/{name}',
         'state': 'uploaded', 'size': 10000, 'digest': 'sha256:' + hashlib.sha256(b'x' * 10000).hexdigest()}]}

class Updates(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows installer lifecycle')
    def test_installer_reserves_worker_and_preserves_restart_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = BackupManager(directory, client=FakeCloud(), state_dir=directory, port=9876)
            manager.update_exit = Mock()
            ready, resume = threading.Event(), threading.Event()
            def download(info):
                ready.set()
                self.assertTrue(resume.wait(3))
                return Path(directory) / 'setup.exe'
            try:
                with patch.object(updater.sys, 'frozen', True, create=True), patch.object(manager.updater, 'refresh', return_value={'available': True}), patch.object(manager.updater, 'download', side_effect=download), patch.object(updater, 'launch_installer') as launch:
                    self.assertTrue(manager.handle('update-install', {})['started'])
                    self.assertTrue(ready.wait(3))
                    with self.assertRaises(BackupError): manager.start('sync')
                    with self.assertRaises(BackupError): manager.browse()
                    resume.set()
                    self.assertTrue(manager.work_lock.acquire(timeout=3))
                    manager.work_lock.release()
                    self.assertIn('--serve', launch.call_args.args[1])
                    self.assertIn(str(manager.state_dir), launch.call_args.args[1])
                    self.assertIn('9876', launch.call_args.args[1])
                    self.assertIn('--no-browser', launch.call_args.args[1])
                    manager.update_exit.assert_called_once()
            finally:
                resume.set()
                manager.close()

    @unittest.skipUnless(os.name == 'nt', 'Windows installer lifecycle')
    def test_failed_install_download_leaves_program_running(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = BackupManager(directory, client=FakeCloud(), state_dir=directory)
            manager.update_exit = Mock()
            try:
                with patch.object(updater.sys, 'frozen', True, create=True), patch.object(manager.updater, 'refresh', return_value={'available': True}), patch.object(manager.updater, 'download', side_effect=OSError()), patch.object(updater, 'launch_installer') as launch:
                    manager.handle('update-install', {})
                    self.assertTrue(manager.work_lock.acquire(timeout=3))
                    manager.work_lock.release()
                    manager.update_exit.assert_not_called()
                    launch.assert_not_called()
                    self.assertFalse(manager.shutdown.is_set())
                    self.assertFalse(manager.updater.installing)
                    self.assertIn('error', manager.updater.snapshot())
            finally: manager.close()

    def test_thirty_day_boundary_and_numeric_versions(self):
        at = datetime.fromisoformat('2026-11-01T00:00:00+00:00').timestamp()
        self.assertFalse(updater.select_release(release(), '1.7.5', at + updater.WINDOW - 1)['mandatory'])
        self.assertTrue(updater.select_release(release(), '1.7.5', at + updater.WINDOW)['mandatory'])
        self.assertGreater(updater.version('1.10.0'), updater.version('1.9.9'))
        self.assertFalse(updater.select_release(release(), '1.8.0', at)['available'])
        self.assertFalse(updater.select_release(release() | {'prerelease': True}, '1.7.5', at)['available'])

    def test_only_exact_asset_digest_and_https_hosts(self):
        data = release()
        data['assets'][0]['browser_download_url'] = 'https://evil.invalid/update.exe'
        self.assertFalse(updater.select_release(data, '1.7.5')['available'])
        data = release()
        data['assets'][0]['digest'] = None
        with self.assertRaises(ValueError): updater.select_release(data, '1.7.5')
        for url in ['http://github.com/file', 'https://github.com.evil.invalid/file', 'https://user@github.com/file', 'https://github.com:444/file', 'file:///test']:
            with self.assertRaises(ValueError): updater.safe_url(url)

    def test_verified_download_and_cleanup_after_corruption(self):
        info = updater.select_release(release(), '1.7.5')
        with tempfile.TemporaryDirectory() as directory:
            service = updater.Updater(directory, '1.7.5')
            with patch.object(updater, 'open_url', return_value=io.BytesIO(b'x' * 10000)):
                path = service.download(info)
                self.assertEqual(path.read_bytes(), b'x' * 10000)
            path.unlink()
            with patch.object(updater, 'open_url', return_value=io.BytesIO(b'y' * 10000)):
                with self.assertRaises(ValueError): service.download(info)
            self.assertFalse(path.exists())
            self.assertFalse(list(Path(directory).glob('*.part')))
            with patch.object(updater, 'open_url', return_value=io.BytesIO(b'x' * 10001)):
                with self.assertRaises(ValueError): service.download(info)

    def test_offline_cache_keeps_deadline_and_backend_blocks_sync(self):
        info = updater.select_release(release(), '1.7.5')
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / 'updates'
            cache.mkdir()
            (cache / 'update-release.json').write_text(json.dumps(info))
            service = updater.Updater(cache, '1.7.5')
            with patch.object(updater.time, 'time', return_value=info['deadline']), patch.object(updater, 'check', side_effect=OSError()):
                self.assertTrue(service.refresh()['mandatory'])
                self.assertIn('error', service.snapshot())
                manager = BackupManager(directory, client=FakeCloud(), state_dir=directory)
                try:
                    with self.assertRaises(BackupError) as error: manager.start('sync')
                    self.assertEqual(error.exception.status, 426)
                    with patch.object(updater, 'check') as fetch:
                        self.assertTrue(manager.public()['update']['mandatory'])
                        fetch.assert_not_called()
                finally: manager.close()
