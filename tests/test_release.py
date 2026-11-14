"""Release behavior and new local API boundaries, with synthetic data only."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from hamrahi_backup import engine as cb
from hamrahi_backup.startup import StartupRegistration
from test_engine import FakeCloud


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / 'منبع + اصلی'
        self.source.mkdir()
        self.cloud = FakeCloud()
        self.manager = cb.BackupManager(self.base, self.cloud)
        self.manager.save_config(self.manager.config | {'settleSeconds': 0, 'exclude': ['.env'],
                                 'folders': [{'path': str(self.source), 'name': 'ignored alias'}]})

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def wait(self):
        self.assertTrue(self.manager.work_lock.acquire(timeout=10), 'worker did not stop')
        self.manager.work_lock.release()

    def test_default_cloud_root_is_computer_name(self):
        with patch.object(cb.socket, 'gethostname', return_value='TEST-COMPUTER'):
            other = cb.BackupManager(self.base, self.cloud, state_dir=self.base / 'other-state')
            self.assertEqual(other.config['remoteRoot'], 'TEST-COMPUTER')
            other.close()

    def test_cloud_folder_names_are_exact_and_alias_is_ignored(self):
        nested = self.source / 'زیرپوشه + #1'
        nested.mkdir()
        (nested / 'hello.txt').write_text('synthetic')
        self.manager.sync()
        names = [x['name'] for x in self.cloud.objects.values() if not x.get('key')]
        self.assertIn(self.source.name, names)
        self.assertIn(nested.name, names)
        self.assertNotIn('ignored alias', names)
        self.assertEqual(self.manager.config['folders'][0]['name'], self.source.name)
        self.assertTrue(all(step['status'] == 'ok' for step in self.manager.state['steps']))

    def test_two_identically_named_sources_cannot_merge(self):
        other = self.base / 'another' / self.source.name
        other.mkdir(parents=True)
        with self.assertRaises(cb.BackupError):
            self.manager.save_config(self.manager.config | {
                'folders': [{'path': str(self.source)}, {'path': str(other)}]})

    def test_all_files_are_paginated_and_persist_across_restart(self):
        expected = set()
        for i in range(125):
            path = self.source / f'{i:03}.txt'
            path.write_text(str(i))
            expected.add(str(path))
        self.manager.sync(preview=True)
        run = self.manager.state['lastResult']['id']
        first = self.manager.items({'run': run})
        second = self.manager.items({'run': run, 'offset': 100})
        self.assertEqual(first['total'], 125)
        self.assertEqual(len(first['items']), 100)
        self.assertEqual(len(second['items']), 25)
        self.assertEqual({item['path'] for item in first['items'] + second['items']}, expected)
        replacement = cb.BackupManager(self.base, self.cloud)
        self.assertEqual(replacement.items({'run': run})['total'], 125)
        replacement.close()

    def test_retry_processes_only_failed_file_and_keeps_original_report(self):
        failed = self.source / 'failed.txt'
        unrelated = self.source / 'other.txt'
        failed.write_text('failed once')
        unrelated.write_text('before')
        snapshot = self.manager.snapshot
        def read(path, target):
            if path == failed:
                raise cb.BackupError('synthetic read failure')
            return snapshot(path, target)
        with patch.object(self.manager, 'snapshot', side_effect=read):
            self.manager.sync()
        run = self.manager.state['lastResult']['id']
        failures = self.manager.items({'run': run, 'failedOnly': True})
        self.assertEqual([x['path'] for x in failures['items']], [str(failed)])
        unrelated.write_text('must wait for full sync')
        self.manager.handle('retry', {'run': run, 'path': str(failed)})
        self.wait()
        result = self.manager.state['lastResult']
        self.assertTrue(result['ok'])
        self.assertTrue(result['retry'])
        self.assertEqual(result['files'], 1)
        self.assertEqual(self.manager.items({'run': result['id']})['items'][0]['path'], str(failed))
        self.assertEqual(self.manager.items({'run': run, 'failedOnly': True})['total'], 1)
        self.assertFalse(any(x.get('data') == b'must wait for full sync' for x in self.cloud.objects.values()))

    def test_fatal_failure_preserves_successes_and_marks_unprocessed_files(self):
        for name in ['a.txt', 'b.txt', 'c.txt']:
            (self.source / name).write_text(name)
        snapshot = self.manager.snapshot
        committed = threading.Event()
        update = self.manager.update
        def observe(**values):
            update(**values)
            if values.get('uploaded') == 1:
                committed.set()
        self.manager.update = observe
        def read(path, target):
            if path.name == 'b.txt':
                self.assertTrue(committed.wait(5))
                raise cb.BackupError('synthetic network error', 502)
            if path.name == 'c.txt':
                self.assertTrue(committed.wait(5))
                self.manager.cancelled()
            return snapshot(path, target)
        with patch.object(self.manager, 'snapshot', side_effect=read):
            self.manager.start('sync')
            self.wait()
        result = self.manager.state['lastResult']
        self.assertFalse(result['ok'])
        self.assertGreaterEqual(result['uploaded'], 1)
        rows = self.manager.items({'run': result['id']})['items']
        statuses = {Path(item['path']).name: item['status'] for item in rows}
        self.assertEqual(statuses['a.txt'], 'ok')
        self.assertEqual(statuses['b.txt'], 'error')
        self.assertIn(statuses['c.txt'], ('ok', 'deferred'))
        self.assertTrue(any(step['status'] == 'error' for step in self.manager.state['steps']))
        self.assertFalse((self.manager.state_dir / 'active-run.json').exists())

    def test_interrupted_run_is_recovered_once(self):
        run = uuid.uuid4().hex
        good, pending = self.source / 'good.txt', self.source / 'pending.txt'
        good.write_text('good')
        pending.write_text('pending')
        result = {'id': run, 'at': cb.now(), 'preview': False, 'uploaded': 0, 'unchanged': 0,
                  'bytes': 0, 'files': 0, 'errors': [], 'excluded': 0, 'samples': [], 'manifests': []}
        cb.atomic_json(self.manager.state_dir / 'active-run.json', result)
        self.manager.record_item(run, good, 'ok', 4)
        self.manager.record_item(run, pending, 'processing')
        replacement = cb.BackupManager(self.base, self.cloud)
        self.assertEqual(replacement.history[0]['uploaded'], 1)
        self.assertFalse(replacement.history[0]['ok'])
        self.assertEqual(replacement.items({'run': run, 'failedOnly': True})['total'], 1)
        self.assertFalse((self.manager.state_dir / 'active-run.json').exists())
        replacement.close()

    def test_retry_and_file_links_reject_arbitrary_or_excluded_files(self):
        run = uuid.uuid4().hex
        outside = self.base / 'outside.txt'
        secret = self.source / '.env'
        for path in (outside, secret):
            path.write_text('synthetic')
            self.manager.record_item(run, path, 'error')
            for action in ('retry', 'reveal'):
                with self.assertRaises(cb.BackupError):
                    self.manager.handle(action, {'run': run, 'path': str(path)})
        with self.assertRaises(cb.BackupError):
            self.manager.handle('retry', {'run': run, 'path': str(self.source / 'unrecorded.txt')})

    def test_items_reject_invalid_identifiers_and_paging(self):
        for data in [{'run': '../private'}, {'run': uuid.uuid4().hex, 'offset': -1},
                     {'run': uuid.uuid4().hex, 'offset': True}]:
            with self.assertRaises(cb.BackupError):
                self.manager.items(data)

    @unittest.skipUnless(os.name == 'nt', 'Windows Explorer')
    def test_file_link_selects_in_explorer_without_executing_file(self):
        path = self.source / 'script.cmd'
        path.write_text('do not execute')
        run = uuid.uuid4().hex
        self.manager.record_item(run, path, 'error')
        with patch.object(cb.subprocess, 'Popen') as launch:
            self.manager.handle('reveal', {'run': run, 'path': str(path)})
        args = launch.call_args.args[0]
        self.assertEqual(Path(args[0]).name, 'explorer.exe')
        self.assertEqual(args[1:], ['/select,', str(path.resolve())])

    def test_startup_toggle_rolls_back_if_config_write_fails(self):
        writes = []
        with patch.object(self.manager.startup, 'read', return_value='previous-command'), \
             patch.object(self.manager.startup, 'write', side_effect=writes.append), \
             patch.object(cb, 'atomic_json', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                self.manager.save_config(self.manager.config | {'startupEnabled': True})
        self.assertEqual(writes[-1], 'previous-command')
        self.assertFalse(self.manager.config['startupEnabled'])

    def test_startup_is_opt_in_and_uses_correct_data_directory_and_port(self):
        with patch.object(self.manager.startup, 'read', return_value=None), \
             patch.object(self.manager.startup, 'write') as write:
            self.manager.save_config(self.manager.config | {'startupEnabled': True})
            self.assertIn('--no-browser', write.call_args.args[0])
            self.manager.save_config(self.manager.config | {'startupEnabled': False})
            self.assertIsNone(write.call_args.args[0])
        registration = StartupRegistration(self.base / 'private state', 8799)
        with patch('hamrahi_backup.startup.subprocess.list2cmdline', wraps=subprocess.list2cmdline) as quote:
            command = registration.command()
        self.assertEqual(quote.call_args_list[0].args[0][3], str((self.base / 'private state').resolve()))
        self.assertIn('--port 8799', command)
        self.assertLessEqual(len(command), 260)

    def test_startup_permission_failure_does_not_save_checkbox(self):
        before = self.manager.config_path.read_bytes()
        with patch.object(self.manager.startup, 'read', return_value=None), \
             patch.object(self.manager.startup, 'configure', side_effect=PermissionError()):
            with self.assertRaises(cb.BackupError):
                self.manager.save_config(self.manager.config | {'startupEnabled': True})
        self.assertEqual(self.manager.config_path.read_bytes(), before)
