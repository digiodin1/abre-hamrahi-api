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
            'automatic': True, 'debounceSeconds': 1, 'settleSeconds': 0,
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
