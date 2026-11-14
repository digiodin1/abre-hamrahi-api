"""Automatic sync tests use synthetic files and an in-memory cloud."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from hamrahi_backup.engine import BackupError, BackupManager
from hamrahi_backup.watcher import PollingObserver, relevant_path
from test_engine import FakeCloud


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.fake = FakeCloud()
        self.engine = BackupManager(self.base, self.fake, state_dir=self.base / 'state')
        self.engine.save_config(self.engine.config | {
            'syncBehavior': 'versioned', 'automatic': True, 'debounceSeconds': 1, 'settleSeconds': 0,
            'exclude': ['.env', 'node_modules', '.qa', '.private', '.runtime', '.wwebjs_auth', '*.dpapi'],
            'folders': [{'path': str(self.source), 'name': 'Example'}]})

    def tearDown(self):
        self.engine.close()
        self.assertTrue(self.engine.work_lock.acquire(timeout=5))
        self.engine.work_lock.release()
        self.temp.cleanup()

    def await_worker(self):
        self.assertTrue(self.engine.work_lock.acquire(timeout=5))
        self.engine.work_lock.release()

    def make_due(self):
        self.engine.change_due = time.time() - 1
        self.engine.retry_at = 0

    def test_changes_debounce_and_exclusions_do_not_delay_queue(self):
        self.engine.synced_generation = self.engine.change_generation
        before = time.time()
        self.engine._on_source_change(self.source / 'hello.txt')
        self.assertGreaterEqual(self.engine.change_due, before + 1)
        self.assertLess(self.engine.change_due, before + 2)
        generation = self.engine.change_generation
        self.engine._on_source_change(self.source / '.env')
        self.engine._on_source_change(self.source / 'node_modules' / 'file.txt')
        self.engine._on_source_change(self.engine.state_dir / 'history.json')
        self.assertEqual(self.engine.change_generation, generation)
        before = time.time()
        self.engine._on_source_change(self.source / 'hello.txt')
        self.assertGreaterEqual(self.engine.change_due, before + 1)
        self.assertLess(self.engine.change_due, before + 2)
        self.assertTrue(self.engine.public()['state']['pendingSync'])

    def test_scheduler_uploads_changed_content_without_manual_start(self):
        target = self.source / 'hello.txt'
        target.write_text('first')
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        self.assertTrue(self.engine.state['lastResult']['ok'])
        self.assertFalse(self.engine.public()['state']['pendingSync'])
        next_full = self.engine.state['nextRun']
        target.write_text('second')
        self.engine._on_source_change(target)
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        data = [obj['data'] for obj in self.fake.objects.values()
                if obj.get('key') and not obj['name'].startswith('_manifest')]
        self.assertCountEqual(data, [b'first', b'second'])
        self.assertEqual(self.engine.state['nextRun'], next_full)

    def test_http_500_recovery_adopts_ambiguous_upload_without_duplicates(self):
        for name in ('a.txt', 'b.txt', 'c.txt'):
            (self.source / name).write_text('synthetic ' + name)
        upload = self.fake.upload
        failed = False
        def interrupted(path, name, parent, **kwargs):
            nonlocal failed
            result = upload(path, name, parent, **kwargs)
            if name == 'b.txt' and not failed:
                failed = True
                raise BackupError('پاسخ ناموفق ابرهمراهی؛ HTTP 500', 502)
            return result
        self.fake.upload = interrupted
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        state = self.engine.public()['state']
        self.assertFalse(state['lastResult']['ok'])
        self.assertEqual(state['recovery']['attempt'], 1)
        self.assertEqual(state['progress']['totalBytes'], 45)
        self.assertEqual(state['progress']['failed'], 1)
        self.assertLess(state['progress']['percent'], 100)
        self.assertEqual(state['progress']['successful'] + state['progress']['failed'] + state['progress']['waiting'], 3)
        rows = self.engine.items({'run': state['lastResult']['id'], 'failedOnly': True})['items']
        self.assertEqual(next(row['status'] for row in rows if Path(row['path']).name == 'b.txt'), 'error')
        self.assertTrue(all('HTTP 500' in row['error'] for row in rows))
        pending_path = rows[0]['path']
        self.engine.selected_item({'run': state['lastResult']['id'], 'path': pending_path}, require_error=True)
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        state = self.engine.public()['state']
        self.assertTrue(state['lastResult']['ok'])
        self.assertEqual(state['progress']['percent'], 100)
        self.assertEqual(state['recovery']['attempt'], 0)
        files = [obj for obj in self.fake.objects.values() if obj.get('key') and not obj['name'].startswith('_manifest')]
        self.assertCountEqual([obj['name'] for obj in files], ['a.txt', 'b.txt', 'c.txt'])
        self.assertIn(state['lastResult']['uploaded'], (0, 1))

    def test_change_during_upload_remains_pending_for_another_run(self):
        target = self.source / 'hello.txt'
        target.write_text('first')
        upload = self.fake.upload
        fired = False

        def change_after_upload(*args, **kwargs):
            nonlocal fired
            result = upload(*args, **kwargs)
            if not fired:
                fired = True
                target.write_text('second')
                self.engine._on_source_change(target)
            return result

        self.fake.upload = change_after_upload
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        self.assertTrue(self.engine.public()['state']['pendingSync'])
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        self.assertFalse(self.engine.public()['state']['pendingSync'])

    def test_pause_resume_and_missing_login_block_automatic_uploads(self):
        (self.source / 'hello.txt').write_text('first')
        self.engine.handle('pause', {})
        self.make_due()
        self.engine.scheduler_tick()
        self.assertEqual(self.fake.uploads, 0)
        self.assertTrue(self.engine.public()['config']['automaticPaused'])
        self.engine.handle('resume', {})
        self.fake.tokens = {}
        self.make_due()
        self.engine.scheduler_tick()
        self.assertEqual(self.fake.uploads, 0)
        self.fake.tokens = {'access': 'synthetic'}
        self.engine.scheduler_tick()
        self.await_worker()
        self.assertTrue(self.engine.state['lastResult']['ok'])

    def test_failed_upload_is_retained_and_retried_after_backoff(self):
        (self.source / 'hello.txt').write_text('first')
        connection = self.fake.connection
        self.fake.connection = lambda: (_ for _ in ()).throw(BackupError('offline', 502))
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        self.assertTrue(self.engine.public()['state']['pendingSync'])
        self.assertGreater(self.engine.retry_at, time.time())
        self.fake.connection = connection
        self.engine.scheduler_tick()
        self.assertEqual(self.fake.uploads, 0)
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        self.assertTrue(self.engine.state['lastResult']['ok'])

    def test_periodic_check_wins_over_change_queue_and_detects_corruption(self):
        target = self.source / 'hello.txt'
        target.write_text('first')
        self.engine.sync()
        obj = next(item for item in self.fake.objects.values()
                   if item.get('key') and not item['name'].startswith('_manifest'))
        obj['data'] = b'wrong'
        self.engine._on_source_change(target)
        self.make_due()
        self.engine.state['nextRun'] = time.time() - 1
        self.engine.scheduler_tick()
        self.await_worker()
        self.assertEqual(self.engine.state['trigger'], 'schedule')
        self.assertFalse(self.engine.state['lastResult']['ok'])

    def test_partial_success_cannot_hide_failed_full_reconciliation(self):
        target = self.source / 'a.txt'
        other = self.source / 'b.txt'
        target.write_text('first')
        other.write_text('other')
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        remote = next(item for item in self.fake.objects.values() if item.get('name') == 'b.txt')
        remote['data'] = b'wrong'
        target.write_text('changed')
        self.engine._on_source_change(target)
        self.engine.state['nextRun'] = time.time() - 1
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        self.assertFalse(self.engine.state['lastResult']['ok'])
        self.assertGreater(self.engine.full_sync_generation, self.engine.synced_generation)
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        self.assertFalse(self.engine.state['lastResult']['partial'])
        self.assertFalse(self.engine.state['lastResult']['ok'])
        self.assertTrue(self.engine.public()['state']['pendingSync'])

    def test_fallback_observer_detects_a_real_filesystem_change(self):
        event = threading.Event()
        target = self.source / 'hello.txt'
        observer = PollingObserver(lambda path: event.set(), [self.source], self.engine.config, self.engine.state_dir)
        initial_scan = threading.Event()
        snapshot = observer._snapshot

        def scan():
            result = snapshot()
            initial_scan.set()
            return result

        observer._snapshot = scan
        observer.start()
        try:
            self.assertTrue(initial_scan.wait(5))
            target.write_text('filesystem change')
            self.assertTrue(event.wait(6))
        finally:
            observer.stop()
            observer.join(5)
        self.assertFalse(observer.is_alive())

    def test_sqlite_wal_notification_maps_to_database(self):
        target = self.source / 'database.sqlite'
        self.assertEqual(relevant_path(str(target) + '-wal', self.engine.config, self.engine.state_dir), target.resolve())

    def test_changed_file_only_run_does_not_snapshot_unrelated_files(self):
        target, other = self.source / 'a.txt', self.source / 'b.txt'
        target.write_text('before')
        other.write_text('unrelated')
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        target.write_text('after')
        self.engine._on_source_change(target)
        with patch.object(self.engine, 'snapshot', wraps=self.engine.snapshot) as snapshot:
            self.make_due()
            self.engine.scheduler_tick()
            self.await_worker()
        self.assertEqual([call.args[0].resolve() for call in snapshot.call_args_list], [target.resolve()])
        self.assertEqual(self.engine.state['lastResult']['files'], 1)
        self.assertTrue(self.engine.state['lastResult']['partial'])
        self.assertFalse(self.engine.state['lastResult']['retry'])
        self.assertFalse(self.engine.public()['state']['pendingSync'])
        self.assertEqual(self.engine.public()['syncMatch']['percent'], 100)
        self.assertTrue(self.engine.public()['syncMatch']['stale'])
        self.engine.state['nextRun'] = time.time() - 1
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        self.assertFalse(self.engine.public()['state']['pendingSync'])
        self.assertEqual(self.engine.public()['syncMatch']['percent'], 100)

    def test_change_during_partial_run_is_not_cleared(self):
        target = self.source / 'a.txt'
        target.write_text('before')
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        target.write_text('after')
        self.engine._on_source_change(target)
        upload = self.fake.upload
        def edit(*args, **kwargs):
            result = upload(*args, **kwargs)
            if args[1] == 'a.txt':
                target.write_text('newest')
                self.engine._on_source_change(target)
            return result
        with patch.object(self.fake, 'upload', side_effect=edit):
            self.make_due()
            self.engine.scheduler_tick()
            self.await_worker()
        self.assertTrue(self.engine.public()['state']['pendingSync'])
        self.assertIn(str(target.resolve()), self.engine.pending_paths)
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        self.assertFalse(self.engine.public()['state']['pendingSync'])
        self.assertTrue(any(item.get('data') == b'newest' for item in self.fake.objects.values()))

    def test_directory_delete_requests_full_reconciliation(self):
        target = self.source / 'gone.txt'
        target.write_text('before')
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        target.unlink()
        self.engine._on_source_change(target)
        self.assertGreater(self.engine.full_sync_generation, self.engine.synced_generation)
        self.make_due()
        self.engine.scheduler_tick()
        self.await_worker()
        self.assertFalse(self.engine.state['lastResult']['retry'])
        self.assertTrue(any(item.get('data') == b'before' for item in self.fake.objects.values()))

    def test_path_queue_overflow_falls_back_to_full_scan(self):
        self.engine.pending_paths = {str(self.source / f'{i}.txt'): 1 for i in range(1000)}
        with self.engine.lock:
            self.engine._queue_sync(self.source / 'overflow.txt')
        self.assertEqual(self.engine.pending_paths, {})
        self.assertGreater(self.engine.full_sync_generation, self.engine.synced_generation)

    def test_new_private_exclusions_apply_to_scan_and_notifications(self):
        for folder in ['.qa', '.private', '.runtime', '.wwebjs_auth']:
            directory = self.source / folder
            directory.mkdir()
            (directory / 'synthetic.txt').write_text('private')
        secret = self.source / 'token.dpapi'
        secret.write_text('private')
        public = self.source / 'public.txt'
        public.write_text('public')
        errors, skips = [], []
        eligible = list(self.engine.walk(self.source.resolve(), self.engine.config, errors, skips))
        self.assertEqual(eligible, [public.resolve()])
        self.assertIsNone(relevant_path(secret, self.engine.config, self.engine.state_dir))

    def test_scheduler_survives_transient_monitor_exception(self):
        recovered = threading.Event()
        original = self.engine.configure_change_monitor
        calls = 0
        def configure():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError('synthetic private detail must not appear')
            original()
            recovered.set()
        with patch.object(self.engine, 'configure_change_monitor', side_effect=configure):
            worker = threading.Thread(target=self.engine.scheduler, daemon=True)
            worker.start()
            try:
                self.assertTrue(recovered.wait(9))
                self.assertTrue(worker.is_alive())
                self.assertNotIn('synthetic', str(self.engine.public()['state']['watchError']))
            finally:
                self.engine.close()
                worker.join(5)

    def test_scheduler_and_monitor_detect_edit_and_upload_without_api_call(self):
        target = self.source / 'hello.txt'
        target.write_text('first')
        scheduler = threading.Thread(target=self.engine.scheduler, daemon=True)
        scheduler.start()

        def uploaded(content):
            return any(item.get('data') == content for item in list(self.fake.objects.values()))

        def wait_until(condition):
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if condition():
                    return True
                self.engine.shutdown.wait(0.05)
            return False

        try:
            self.assertTrue(wait_until(lambda: uploaded(b'first') and not self.engine.public()['state']['busy']))
            target.write_text('second')
            self.assertTrue(wait_until(lambda: uploaded(b'second')))
            self.assertTrue(uploaded(b'first'))
        finally:
            self.engine.close()
            scheduler.join(6)
        self.assertFalse(scheduler.is_alive())

    @unittest.skipUnless(os.name == 'nt', 'Windows folder picker')
    def test_folder_picker_handles_unicode_cancellation_errors_and_concurrency(self):
        target = self.source / 'پوشه'
        target.mkdir()
        payload = json.dumps({'path': str(target), 'cancelled': False}, ensure_ascii=False).encode()
        result = subprocess.CompletedProcess([], 0, stdout=payload, stderr=b'')
        with patch('hamrahi_backup.engine.subprocess.run', return_value=result) as run:
            self.assertEqual(self.engine.browse()['path'], str(target))
            self.assertIn('-I', run.call_args.args[0])
            self.assertTrue(run.call_args.args[0][-1].endswith('folder_picker.py'))
        result.stdout = b'{"path":"","cancelled":true}'
        with patch('hamrahi_backup.engine.subprocess.run', return_value=result):
            self.assertTrue(self.engine.browse()['cancelled'])
        result.returncode = 1
        with patch('hamrahi_backup.engine.subprocess.run', return_value=result):
            with self.assertRaises(BackupError):
                self.engine.browse()
        self.engine.browse_lock.acquire()
        try:
            with self.assertRaises(BackupError) as error:
                self.engine.browse()
            self.assertEqual(error.exception.status, 409)
        finally:
            self.engine.browse_lock.release()
