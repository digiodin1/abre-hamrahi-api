import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from hamrahi_backup.engine import BackupError, BackupManager, HamrahiClient
from test_engine import FakeCloud


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cloud = FakeCloud()
        self.manager = BackupManager(Path(self.temp.name), self.cloud)
        self.gate = threading.Event()

    def tearDown(self):
        self.gate.set()
        if self.manager.quota_thread:
            self.manager.quota_thread.join(3)
            self.assertFalse(self.manager.quota_thread.is_alive())
        self.manager.close()
        self.temp.cleanup()

    def finish(self):
        self.manager.quota_thread.join(3)
        self.assertFalse(self.manager.quota_thread.is_alive())

    def test_refresh_updates_account_quota_without_interrupting_upload(self):
        self.manager.update(busy=True, phase='sending', bytes=123)
        self.manager.work_lock.acquire()
        self.cloud.quota = Mock(return_value={'used': 750, 'total': 1000, 'checkedAt': 'fresh'})
        try:
            self.assertTrue(self.manager.handle('connection', {})['started'])
            self.finish()
            self.assertEqual(self.manager.state['connection']['used'], 750)
            self.assertEqual(self.manager.state['phase'], 'sending')
            self.assertTrue(self.manager.state['busy'])
            self.assertEqual(self.manager.state['bytes'], 123)
            self.assertTrue(self.manager.work_lock.locked())
            self.cloud.quota.assert_called_once_with(refresh=False)
            self.assertFalse(self.manager.public()['state']['quotaStale'])
        finally:
            self.manager.work_lock.release()

    def test_overlapping_requests_are_coalesced(self):
        entered = threading.Event()
        def quota(refresh=True):
            entered.set()
            self.gate.wait(2)
            return self.cloud.connection()
        self.cloud.quota = Mock(side_effect=quota)
        self.manager.refresh_quota()
        self.assertTrue(entered.wait(1))
        self.assertFalse(self.manager.refresh_quota(force=True)['started'])
        self.assertTrue(self.manager.state['quotaRefreshing'])
        self.gate.set()
        self.finish()
        self.cloud.quota.assert_called_once()

    def test_periodic_refresh_uses_cache_and_can_run_again_when_due(self):
        self.cloud.quota = Mock(side_effect=lambda **kwargs: self.cloud.connection())
        self.manager.refresh_quota()
        self.finish()
        self.assertFalse(self.manager.refresh_quota()['started'])
        self.manager.quota_next = 0
        self.assertTrue(self.manager.refresh_quota()['started'])
        self.finish()
        self.assertEqual(self.cloud.quota.call_count, 2)

    def test_failure_retains_previous_quota_and_marks_it_stale(self):
        previous = {'used': 200, 'total': 1000, 'checkedAt': 'earlier'}
        self.manager.set_connection(previous)
        self.cloud.quota = Mock(side_effect=BackupError('offline', 502))
        self.manager.refresh_quota(force=True)
        self.finish()
        self.assertEqual(self.manager.state['connection'], previous)
        self.assertTrue(self.manager.state['quotaError'])
        self.assertTrue(self.manager.public()['state']['quotaStale'])
        self.assertFalse(self.manager.state['quotaRefreshing'])
        self.assertGreater(self.manager.quota_next, time.monotonic() + 50)

    def test_rate_limit_backs_off_without_changing_backup_errors(self):
        self.manager.update(errors=['existing file error'])
        self.cloud.quota = Mock(side_effect=BackupError('rate limit', 429))
        self.manager.refresh_quota()
        self.finish()
        self.assertGreater(self.manager.quota_next, time.monotonic() + 290)
        self.assertEqual(self.manager.state['errors'], ['existing file error'])

    def test_response_for_old_session_cannot_repopulate_current_account(self):
        entered = threading.Event()
        def quota(refresh=True):
            entered.set()
            self.gate.wait(2)
            return {'used': 999, 'total': 1000}
        self.cloud.quota = quota
        self.manager.refresh_quota()
        self.assertTrue(entered.wait(1))
        self.cloud.tokens = {}
        self.manager.update(connection=None)
        self.gate.set()
        self.finish()
        self.assertIsNone(self.manager.state['connection'])

    def test_aged_value_is_explicitly_stale_and_public_reads_do_not_fetch(self):
        self.manager.set_connection({'used': 100, 'total': 1000})
        self.manager.quota_checked -= 91
        self.cloud.quota = Mock(side_effect=AssertionError('public performed network request'))
        self.assertTrue(self.manager.public()['state']['quotaStale'])
        self.cloud.quota.assert_not_called()

    def test_logged_out_and_stopped_apps_do_not_poll(self):
        self.cloud.tokens = {}
        self.assertFalse(self.manager.refresh_quota()['started'])
        with self.assertRaises(BackupError):
            self.manager.handle('connection', {})
        self.cloud.tokens = {'access': 'synthetic'}
        self.manager.close()
        self.assertFalse(self.manager.refresh_quota()['started'])


class ProviderQuotaTests(unittest.TestCase):
    def test_quota_uses_only_provider_account_totals_and_does_not_refresh_tokens(self):
        client = object.__new__(HamrahiClient)
        client._json_once = Mock(return_value={'used': 750, 'total_plan': 1000, 'total_gift': 200})
        quota = client.quota(refresh=False)
        self.assertEqual(quota['used'], 750)
        self.assertEqual(quota['total'], 1200)
        client._json_once.assert_called_once_with('GET', 'v5/rgw/get-quota/', retry=False)

    def test_invalid_provider_numbers_do_not_become_zero_capacity(self):
        client = object.__new__(HamrahiClient)
        for invalid in (-1, None, True, 1.5, 'unknown'):
            with self.subTest(value=invalid):
                client._json_once = Mock(return_value={'used': invalid, 'total_plan': 1000, 'total_gift': 0})
                with self.assertRaises(BackupError):
                    client.quota(refresh=False)
