from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

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
                        {'path': str(self.daily), 'syncMode': 'daily', 'dailyTime': '03:15', 'dailyEndTime': '03:15'}]})
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

    def test_daily_takes_priority_and_keeps_its_end_when_interval_is_due(self):
        self.instant_due()
        self.manager.state['nextRun'] = time.time() - 1
        self.manager.retry_at = time.time() + 300
        self.daily_due()
        self.manager.scheduler_tick()
        self.wait()
        self.assertEqual(self.payloads(), [b'daily payload'])
        self.assertEqual(self.manager.state['trigger'], 'daily')
        _, _, end = self.manager.in_daily_window(self.manager.config['folders'][1])
        self.assertEqual(self.manager.active_window_end, end)
        with patch('hamrahi_backup.engine.time.time', return_value=end):
            with self.assertRaises(BackupError):
                self.manager.cancelled()
        self.manager.retry_at = 0
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
        self.assertGreater(self.manager.daily_retries[self.daily_id][0], time.time())
        self.assertEqual(self.manager.retry_at, 0)
        self.cloud.upload = original
        self.manager.daily_retries.clear()
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
        self.assertTrue(self.manager.state['lastResult']['ok'])
        self.assertGreater(self.manager.folder_schedules[self.daily_id], time.time())
        self.assertEqual(self.payloads(), [b'daily payload'])
        self.assertIn(self.daily_id, self.manager.state['lastResult']['completedJobs'])
        self.assertNotIn(self.instant_id, self.manager.state['lastResult']['completedJobs'])
        self.manager.scheduler_tick()
        self.wait()
        self.assertFalse(self.manager.state['lastResult']['ok'])
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

    def test_scheduled_window_opens_at_configured_minute_and_stays_closed_after_end(self):
        job = self.manager.config['folders'][1]
        job.update(dailyTime='22:02', dailyEndTime='23:59')
        start = datetime(2026, 11, 18, 22, 2).timestamp()
        end = datetime(2026, 11, 18, 23, 59).timestamp()
        self.manager.folder_schedules[self.daily_id] = start
        self.manager.start = Mock()
        self.manager.change_due = self.manager.state['nextRun'] = end + 3600
        with patch('hamrahi_backup.engine.time.time', return_value=start - 1):
            self.manager.scheduler_tick()
        self.manager.start.assert_not_called()
        with patch('hamrahi_backup.engine.time.time', return_value=start):
            self.manager.scheduler_tick()
        self.manager.start.assert_called_once_with('sync', trigger='daily', only_jobs={self.daily_id})
        with patch('hamrahi_backup.engine.time.time', return_value=end):
            self.manager.scheduler_tick()
        self.assertEqual(self.manager.start.call_count, 1)

    def test_window_end_defers_remaining_files_and_next_night_resumes_without_duplicate_upload(self):
        start = (datetime.now() + timedelta(days=1)).replace(hour=22, minute=2, second=0, microsecond=0)
        end = start.replace(hour=23, minute=59).timestamp()
        job = self.manager.config['folders'][1] | {'dailyTime': '22:02', 'dailyEndTime': '23:59'}
        self.manager.save_config(self.manager.config | {'verifyUploads': False, 'uploadWorkers': 1, 'folders': [job]})
        (self.daily / 'b.txt').write_bytes(b'B')
        (self.daily / 'c.txt').write_bytes(b'C')
        self.manager.folder_schedules[self.daily_id] = start.timestamp()
        instant = [start.timestamp()]
        upload = self.cloud.upload
        def upload_until_end(path, name, parent, **options):
            if name == 'b.txt':
                instant[0] = end
                options['cancelled']()
            return upload(path, name, parent, **options)
        with patch('hamrahi_backup.engine.time.time', side_effect=lambda: instant[0]):
            with patch.object(self.cloud, 'upload', side_effect=upload_until_end):
                self.manager.scheduler_tick()
                self.wait()
            status = self.manager.public()
            result = status['state']['lastResult']
            self.assertFalse(result['ok'])
            self.assertTrue(result['windowEnded'])
            self.assertEqual(result['trigger'], 'daily')
            self.assertEqual(result['jobs'], [self.daily_id])
            self.assertEqual(result['errors'], [])
            self.assertEqual(result['uploaded'], 1)
            self.assertEqual(result['deferred'], 2)
            self.assertEqual(status['state']['progress']['waiting'], 2)
            self.assertEqual(status['state']['progress']['failed'], 0)
            self.assertFalse(status['folderStats'][self.daily_id]['inProgress'])
            self.assertIsNone(status['state']['folderSchedules'][self.daily_id]['retryAt'])
            self.manager.scheduler_tick()
            self.wait()
            self.assertEqual(self.cloud.uploads, 1)
            instant[0] = (start + timedelta(days=1)).timestamp()
            self.manager.scheduler_tick()
            self.wait()
        self.assertEqual(self.cloud.uploads, 3)
        self.assertCountEqual(self.payloads(), [b'daily payload', b'B', b'C'])
        self.assertTrue(self.manager.state['lastResult']['ok'])

    def test_daily_window_sends_small_changes_and_missing_cloud_files_before_large_current_files(self):
        start = (datetime.now() + timedelta(days=1)).replace(hour=22, minute=2, second=0, microsecond=0)
        job = self.manager.config['folders'][1] | {'dailyTime': '22:02', 'dailyEndTime': '23:59'}
        self.manager.save_config(self.manager.config | {'verifyUploads': False, 'uploadWorkers': 1, 'folders': [job]})
        large = self.daily / 'large.bin'
        changed = self.daily / 'changed.txt'
        missing = self.daily / 'missing.txt'
        large.write_bytes(b'a' * 16384)
        changed.write_bytes(b'old')
        missing.write_bytes(b'only here')
        self.manager.sync()
        changed.write_bytes(b'new version')
        (self.daily / 'new.txt').write_bytes(b'new')
        self.cloud.objects.pop(next(item['id'] for item in self.cloud.objects.values() if item['name'] == missing.name))
        sent = []
        original = self.cloud.upload
        def send(path, name, parent, **options):
            sent.append(name)
            return original(path, name, parent, **options)
        self.manager.folder_schedules[self.daily_id] = start.timestamp()
        with patch('hamrahi_backup.engine.time.time', return_value=start.timestamp()), \
                patch.object(self.cloud, 'upload', side_effect=send):
            self.manager.scheduler_tick()
            self.wait()
        self.assertCountEqual(sent, ['changed.txt', 'new.txt', 'missing.txt'])
        self.assertIn(b'new version', self.payloads())
        result = self.manager.state['lastResult']
        self.assertTrue(result['ok'])
        self.assertEqual([Path(sample['path']).name for sample in result['samples']][:3], sent)

    def test_nightly_reports_and_file_details_survive_frequent_instant_runs_and_restart(self):
        for number in range(4):
            run = format(number, '032x')
            self.manager.record_item(run, self.daily / 'daily.txt', 'deferred', 1)
            self.manager.finish_result({'id': run, 'trigger': 'daily', 'errors': [], 'windowEnded': True})
        for number in range(4, 38):
            self.manager.finish_result({'id': format(number, '032x'), 'trigger': 'schedule', 'errors': []})
        self.manager.close()
        self.manager = BackupManager(self.base, self.cloud)
        nightly = [entry['id'] for entry in self.manager.public()['history'] if entry['trigger'] == 'daily']
        self.assertEqual(nightly, [format(number, '032x') for number in (3, 2, 1)])
        self.assertEqual(self.manager.items({'run': nightly[-1]})['total'], 1)

    def test_daily_window_supports_overnight_and_daytime_ranges(self):
        overnight = self.manager.config['folders'][1] | {'dailyTime': '20:00', 'dailyEndTime': '08:00'}
        self.assertTrue(self.manager.in_daily_window(overnight, datetime(2026, 11, 16, 7, 59).timestamp())[0])
        self.assertFalse(self.manager.in_daily_window(overnight, datetime(2026, 11, 16, 12, 0).timestamp())[0])
        daytime = overnight | {'dailyTime': '08:00', 'dailyEndTime': '18:00'}
        self.assertTrue(self.manager.in_daily_window(daytime, datetime(2026, 11, 16, 12, 0).timestamp())[0])
        self.assertFalse(self.manager.in_daily_window(daytime, datetime(2026, 11, 16, 19, 0).timestamp())[0])
