from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hamrahi_backup import engine
from test_engine import FakeCloud


class CompleteSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.cloud = FakeCloud()
        self.manager = engine.BackupManager(self.base, self.cloud)
        self.manager.save_config(self.manager.config | {'settleSeconds': 0, 'verifyUploads': False,
            'folders': [{'path': str(self.source)}]})

    def tearDown(self):
        self.manager.close()
        self.temporary.cleanup()

    def test_default_scope_has_no_file_type_name_or_size_exclusions(self):
        self.assertEqual(self.manager.config['exclude'], [])
        self.assertEqual(self.manager.config['maxFileMiB'], 0)
        names = ['.env', 'private.key', 'database.mdf', 'archive.zip', '.git/config',
                 'node_modules/library.js', 'build/output.exe', '.qa/test.txt', 'session.dpapi']
        for name in names:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'synthetic')
        self.manager.sync()
        self.assertTrue(self.manager.state['lastResult']['ok'])
        self.assertEqual(self.manager.state['lastResult']['files'], len(names))
        with self.manager.db() as database:
            actual = {row[0] for row in database.execute('SELECT rel FROM files')}
        self.assertEqual(actual, set(names))

    def test_unlimited_size_accepts_large_metadata_without_allocating_a_gigabyte(self):
        source = self.source / 'large.bin'
        source.write_bytes(b'payload')
        entry = Mock(name='large.bin', path=str(source))
        entry.name = 'large.bin'
        entry.stat.return_value = SimpleNamespace(st_size=3 * 1024 ** 3, st_mtime=0, st_file_attributes=0)
        entry.is_symlink.return_value = False
        entry.is_dir.return_value = False
        entry.is_file.return_value = True
        errors, skips = [], []
        with patch.object(engine.os, 'scandir') as scan:
            scan.return_value.__enter__.return_value = iter([entry])
            self.assertEqual(list(self.manager.walk(self.source, self.manager.config, errors, skips)), [source])
        self.assertEqual(errors, [])

    def test_one_502_does_not_block_later_files(self):
        for number in range(12):
            (self.source / f'{number:02}.txt').write_bytes(b'data')
        upload = self.cloud.upload
        def transfer(path, name, parent, **options):
            if name == '00.txt':
                raise engine.BackupError('synthetic HTTP 500', 502)
            return upload(path, name, parent, **options)
        with patch.object(self.cloud, 'upload', side_effect=transfer):
            self.manager.sync()
        self.assertFalse(self.manager.state['lastResult']['ok'])
        self.assertEqual(self.manager.state['lastResult']['uploaded'], 11)
        self.assertEqual(self.manager.items({'run': self.manager.state['lastResult']['id'],
            'failedOnly': True})['total'], 1)

    def test_empty_nested_directories_keep_their_exact_names(self):
        (self.source / 'Empty Folder' / 'پوشهٔ خالی').mkdir(parents=True)
        self.manager.sync()
        names = {item['name'] for item in self.cloud.objects.values() if not item.get('key')}
        self.assertTrue({'Empty Folder', 'پوشهٔ خالی'}.issubset(names))
        self.assertNotIn('__directory__', names)
        self.assertTrue(self.manager.state['lastResult']['ok'])

    def test_repeated_service_outage_stops_new_requests_for_automatic_backoff(self):
        for number in range(40):
            (self.source / f'{number:02}.txt').write_bytes(b'data')
        def offline(*args, **options):
            raise engine.BackupError('synthetic service outage', 502)
        with patch.object(self.cloud, 'upload', side_effect=offline) as uploads:
            self.manager.start('sync')
            self.assertTrue(self.manager.work_lock.acquire(timeout=10))
            self.manager.work_lock.release()
        self.assertLess(uploads.call_count, 40)
        self.assertFalse(self.manager.state['lastResult']['ok'])
        state = self.manager.public()['state']['progress']
        self.assertGreater(state['waiting'], 0)
        self.assertGreaterEqual(state['failed'], 8)

    def test_recorded_directory_failure_can_be_retried_with_full_reconciliation(self):
        directory = self.source / 'empty'
        directory.mkdir()
        run = 'a' * 32
        self.manager.record_item(run, directory, 'error', error='synthetic permission error')
        with patch.object(self.manager, 'start', return_value={'started': True}) as start:
            self.manager.handle('retry', {'run': run, 'path': str(directory)})
        self.assertEqual(start.call_args.kwargs, {'only_paths': None})

    def test_automatic_reconciliation_reuses_proven_unchanged_fingerprints(self):
        for number in range(70):
            (self.source / f'{number}.txt').write_bytes(b'data')
        self.manager.sync()
        self.manager.state['trigger'] = 'schedule'
        with patch.object(self.manager, 'snapshot', wraps=self.manager.snapshot) as snapshot:
            self.manager.sync()
        snapshot.assert_not_called()
        self.assertEqual(self.manager.state['lastResult']['unchanged'], 70)
        self.assertEqual(self.manager.public()['state']['progress']['percent'], 100)
        self.assertEqual(self.manager.items({'run': self.manager.state['lastResult']['id']})['total'], 70)

    def test_notifications_and_manual_runs_force_hash_and_cloud_deletion_heals(self):
        path = self.source / 'a.txt'
        path.write_bytes(b'one')
        self.manager.sync()
        for trigger, only_paths in [('manual', None), ('change', {str(path.resolve())})]:
            self.manager.state['trigger'] = trigger
            with patch.object(self.manager, 'snapshot', wraps=self.manager.snapshot) as snapshot:
                self.manager.sync(only_paths=only_paths)
            self.assertEqual(snapshot.call_count, 1)
        identifier = next(obj['id'] for obj in self.cloud.objects.values() if obj['name'] == 'a.txt')
        del self.cloud.objects[identifier]
        self.manager.state['trigger'] = 'schedule'
        with patch.object(self.manager, 'snapshot', wraps=self.manager.snapshot) as snapshot:
            self.manager.sync()
        self.assertEqual(snapshot.call_count, 1)
        self.assertEqual(self.manager.state['lastResult']['uploaded'], 1)

    def test_volume_updates_before_run_finishes(self):
        (self.source / 'a.txt').write_bytes(b'1234')
        (self.source / 'b.txt').write_bytes(b'123456')
        update = self.manager.update
        uploaded_first = threading.Event()
        upload = self.cloud.upload
        observed = []
        def transfer(path, name, parent, **options):
            if name == 'b.txt':
                self.assertTrue(uploaded_first.wait(5))
            return upload(path, name, parent, **options)
        def record(**values):
            update(**values)
            if values.get('uploaded'):
                observed.append(self.manager.public()['syncMatch'])
                uploaded_first.set()
        with patch.object(self.manager, 'update', side_effect=record), \
                patch.object(self.cloud, 'upload', side_effect=transfer):
            self.manager.sync()
        self.assertTrue(any(0 < item['percent'] < 100 for item in observed))
        self.assertTrue(all(item['totalBytes'] == 10 for item in observed))

    def test_continuous_notifications_cannot_postpone_start_forever(self):
        path = self.source / 'a.txt'
        self.manager.synced_generation = self.manager.change_generation
        with patch.object(engine.time, 'time', return_value=100):
            self.manager._queue_sync(path)
        for instant in (110, 120, 140, 180):
            with patch.object(engine.time, 'time', return_value=instant):
                self.manager._queue_sync(path)
        self.assertLessEqual(self.manager.change_due, 130)

    def test_size_identity_mismatch_after_upload_cannot_finish_successfully(self):
        (self.source / 'a.txt').write_bytes(b'data')
        listing = self.cloud.list_folder
        def observed(parent=None):
            items = listing(parent)
            if any(item.get('name') == 'a.txt' for item in items):
                return [item | {'size': 0} if item.get('name') == 'a.txt' else item for item in items]
            return items
        with patch.object(self.cloud, 'list_folder', side_effect=observed):
            self.manager.sync()
        self.assertFalse(self.manager.state['lastResult']['ok'])
        self.assertEqual(self.manager.public()['state']['progress']['failed'], 1)

    def test_configured_concurrency_and_large_file_priority(self):
        for number in range(6):
            (self.source / f'{number}.txt').write_bytes(b'x' * (number + 1))
        self.manager.save_config(self.manager.config | {'uploadWorkers': 3})
        barrier, guard = threading.Barrier(3), threading.Lock()
        active, peak = 0, 0
        names = []
        upload = self.cloud.upload
        def transfer(path, name, parent, **options):
            nonlocal active, peak
            if name.startswith('_manifest'):
                return upload(path, name, parent, **options)
            with guard:
                names.append(name)
                active += 1
                peak = max(peak, active)
            try:
                barrier.wait(timeout=5)
                with guard:
                    return upload(path, name, parent, **options)
            finally:
                with guard:
                    active -= 1
        with patch.object(self.cloud, 'upload', side_effect=transfer):
            self.manager.sync()
        self.assertEqual(peak, 3)
        self.assertEqual(set(names[:3]), {'5.txt', '4.txt', '3.txt'})
        self.assertTrue(self.manager.state['lastResult']['ok'])

    def test_existing_wal_named_file_and_database_both_receive_change_notifications(self):
        database = self.source / 'notes'
        sidecar = self.source / 'notes-wal'
        database.write_bytes(b'data')
        sidecar.write_bytes(b'more data')
        self.manager.save_config(self.manager.config | {'automatic': True})
        self.manager.synced_generation = self.manager.change_generation
        self.manager._on_source_change(sidecar)
        self.assertIn(str(sidecar.resolve()), self.manager.pending_paths)
        self.assertIn(str(database.resolve()), self.manager.pending_paths)
        self.assertLessEqual(self.manager.full_sync_generation, self.manager.synced_generation)
