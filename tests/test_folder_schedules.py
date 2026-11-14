from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from hamrahi_backup.engine import BackupError, BackupManager
from test_engine import FakeCloud


class FolderScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.instant = self.base / 'Instant'
        self.daily = self.base / 'Daily'
        self.instant.mkdir()
        self.daily.mkdir()
        (self.instant / 'instant.txt').write_text('instant payload')
        (self.daily / 'daily.txt').write_text('daily payload')
        self.cloud = FakeCloud()
        self.manager = BackupManager(self.base, self.cloud)
        self.manager.save_config(self.manager.config | {
            'automatic': True, 'settleSeconds': 0,
            'folders': [{'path': str(self.instant)},
                        {'path': str(self.daily), 'syncMode': 'daily', 'dailyTime': '03:15'}]})
        self.instant_id, self.daily_id = [job['id'] for job in self.manager.config['folders']]

    def tearDown(self):
        self.manager.close()
        self.wait()
        self.temp.cleanup()

    def wait(self):
        self.assertTrue(self.manager.work_lock.acquire(timeout=5))
        self.manager.work_lock.release()

    def instant_due(self):
        self.manager.change_due = time.time() - 1
        self.manager.state['nextRun'] = time.time() + 3600

    def daily_due(self):
        deadline = time.time() - 60
        self.manager.folder_schedules[self.daily_id] = deadline
        with self.manager.db() as database:
            database.execute('UPDATE folder_schedules SET next_run=? WHERE job=?', (deadline, self.daily_id))

    def payloads(self):
        return [item['data'] for item in self.cloud.objects.values()
                if item.get('key') and not item['name'].startswith('_manifest')]

    def test_legacy_folder_migrates_to_instant_and_daily_time_is_validated(self):
        self.assertEqual(self.manager.config['folders'][0]['syncMode'], 'instant')
        self.assertEqual(self.manager.config['folders'][0]['dailyTime'], '02:00')
        for invalid in ('24:00', '2:00', '12:60', None, '<script>'):
            with self.subTest(value=invalid), self.assertRaises(BackupError):
                self.manager.validate_config(self.manager.config | {'folders': [
                    {'path': str(self.daily), 'syncMode': 'daily', 'dailyTime': invalid}]})
        with self.assertRaises(BackupError):
            self.manager.validate_config(self.manager.config | {'folders': [
                {'path': str(self.daily), 'syncMode': 'unknown'}]})

    def test_daily_change_never_enters_instant_queue(self):
        generation = self.manager.change_generation
        self.manager._on_source_change(self.daily / 'daily.txt')
        self.assertEqual(self.manager.change_generation, generation)
        self.manager._on_source_change(self.instant / 'instant.txt')
        self.assertGreater(self.manager.change_generation, generation)

    def test_watcher_receives_only_instant_roots(self):
        self.manager.change_monitor.configure = Mock(return_value={'watching': 1, 'backend': 'fake', 'error': None})
        self.manager.configure_change_monitor()
        config = self.manager.change_monitor.configure.call_args.args[0]
        self.assertEqual([job['id'] for job in config['folders']], [self.instant_id])

    def test_instant_and_interval_runs_leave_future_daily_folder_alone(self):
        self.instant_due()
        self.manager.scheduler_tick()
        self.wait()
        self.assertEqual(self.payloads(), [b'instant payload'])
        (self.instant / 'later.txt').write_text('interval payload')
        self.manager.state['nextRun'] = time.time() - 1
        self.manager.scheduler_tick()
        self.wait()
        self.assertCountEqual(self.payloads(), [b'instant payload', b'interval payload'])

    def test_daily_due_runs_only_daily_folder_and_preserves_instant_pending_work(self):
        self.manager.change_due = time.time() + 3600
        self.manager.state['nextRun'] = time.time() + 3600
        generation = self.manager.change_generation
        self.daily_due()
        self.manager.scheduler_tick()
        self.wait()
        self.assertEqual(self.payloads(), [b'daily payload'])
        self.assertEqual(self.manager.change_generation, generation)
        self.assertLess(self.manager.synced_generation, generation)
        self.assertGreater(self.manager.folder_schedules[self.daily_id], time.time())
        uploads = self.cloud.uploads
        self.manager.scheduler_tick()
        self.wait()
        self.assertEqual(self.cloud.uploads, uploads)

    def test_simultaneously_due_folders_run_once_and_acknowledge_instant_changes(self):
        self.instant_due()
        self.daily_due()
        self.manager.scheduler_tick()
        self.wait()
        self.assertCountEqual(self.payloads(), [b'instant payload', b'daily payload'])
        self.assertEqual(self.manager.synced_generation, self.manager.change_generation)
        self.assertGreater(self.manager.folder_schedules[self.daily_id], time.time())

    def test_due_time_survives_restart_and_catches_up_after_sleep(self):
        self.daily_due()
        deadline = self.manager.folder_schedules[self.daily_id]
        self.manager.close()
        self.manager = BackupManager(self.base, self.cloud)
        self.assertEqual(self.manager.folder_schedules[self.daily_id], deadline)
        self.manager.scheduler_tick()
        self.wait()
        self.assertEqual(self.payloads(), [b'daily payload'])
        self.assertGreater(self.manager.folder_schedules[self.daily_id], time.time())

    def test_daily_failure_keeps_deadline_and_retries_without_reuploading_successes(self):
        self.manager.change_due = time.time() + 3600
        self.manager.state['nextRun'] = time.time() + 3600
        self.daily_due()
        original = self.cloud.upload
        self.cloud.upload = Mock(side_effect=BackupError('temporary failure', 502))
        self.manager.scheduler_tick()
        self.wait()
        self.assertLess(self.manager.folder_schedules[self.daily_id], time.time())
        self.assertGreater(self.manager.retry_at, time.time())
        self.cloud.upload = original
        self.manager.retry_at = 0
        self.manager.scheduler_tick()
        self.wait()
        self.assertEqual(self.payloads(), [b'daily payload'])
        self.assertGreater(self.manager.folder_schedules[self.daily_id], time.time())

    def test_pause_and_logout_prevent_daily_upload(self):
        self.daily_due()
        self.manager.handle('pause', {})
        self.manager.scheduler_tick()
        self.assertEqual(self.cloud.uploads, 0)
        self.manager.handle('resume', {})
        self.cloud.tokens = {}
        self.manager.scheduler_tick()
        self.assertEqual(self.cloud.uploads, 0)

    def test_another_folder_failure_does_not_repeat_successful_daily_backup(self):
        self.instant_due()
        self.daily_due()
        original = self.cloud.upload
        def fail_instant(path, name, parent, **kwargs):
            if name == 'instant.txt':
                raise BackupError('synthetic instant failure', 403)
            return original(path, name, parent, **kwargs)
        self.cloud.upload = fail_instant
        self.manager.scheduler_tick()
        self.wait()
        self.assertFalse(self.manager.state['lastResult']['ok'])
        self.assertGreater(self.manager.folder_schedules[self.daily_id], time.time())
        self.assertEqual(self.payloads(), [b'daily payload'])
        self.assertIn(self.daily_id, self.manager.state['lastResult']['completedJobs'])
        self.assertNotIn(self.instant_id, self.manager.state['lastResult']['completedJobs'])
        self.manager.retry_at = 0
        self.instant_due()
        self.manager.scheduler_tick()
        self.wait()
        self.assertNotIn(self.daily_id, self.manager.state['lastResult']['completedJobs'])

    def test_manual_sync_includes_daily_folders_before_their_scheduled_time(self):
        self.manager.start('sync')
        self.wait()
        self.assertCountEqual(self.payloads(), [b'instant payload', b'daily payload'])

    def test_changing_daily_time_recomputes_deadline_and_instant_removes_it(self):
        folders = [dict(job) for job in self.manager.config['folders']]
        folders[1]['dailyTime'] = '18:47'
        self.manager.save_config(self.manager.config | {'folders': folders})
        deadline = datetime.fromtimestamp(self.manager.folder_schedules[self.daily_id])
        self.assertEqual((deadline.hour, deadline.minute), (18, 47))
        self.manager.configure_folder_schedules()
        self.assertEqual(self.manager.folder_schedules[self.daily_id], deadline.timestamp())
        folders[1]['syncMode'] = 'instant'
        self.manager.save_config(self.manager.config | {'folders': folders})
        self.assertNotIn(self.daily_id, self.manager.folder_schedules)

    def test_daily_only_configuration_does_not_upload_before_its_deadline(self):
        folder = self.manager.config['folders'][1]
        self.manager.save_config(self.manager.config | {'folders': [folder]})
        self.instant_due()
        self.manager.state['nextRun'] = time.time() - 1
        self.manager.scheduler_tick()
        self.assertEqual(self.cloud.uploads, 0)
        state = self.manager.public()['state']
        self.assertFalse(state['pendingSync'])
        self.assertEqual(state['nextSync'], self.manager.folder_schedules[self.daily_id])
        self.assertEqual(state['folderSchedules'][self.daily_id]['dailyTime'], '03:15')

    def test_daily_time_uses_local_calendar_and_next_day_at_exact_boundary(self):
        current = datetime(2026, 11, 14, 3, 15)
        self.assertEqual(self.manager.next_daily_run('03:15', current.timestamp()),
                         (current + timedelta(days=1)).timestamp())
        before = current - timedelta(seconds=1)
        self.assertEqual(self.manager.next_daily_run('03:15', before.timestamp()), current.timestamp())
