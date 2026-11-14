from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from hamrahi_backup import audit
from hamrahi_backup import engine
from test_engine import FakeCloud


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.cloud = FakeCloud()
        self.manager = engine.BackupManager(self.base, self.cloud)
        self.manager.save_config(self.manager.config | {'settleSeconds': 0, 'exclude': ['.env'],
            'folders': [{'path': str(self.source)}]})
        self.job = self.manager.config['folders'][0]

    def tearDown(self):
        self.temporary.cleanup()

    def summary(self):
        return self.manager.public()['folderStats'][self.job['id']]

    def test_latest_volume_survives_restart_when_final_inventory_fails(self):
        (self.source / 'old.txt').write_bytes(b'old')
        self.manager.sync()
        (self.source / 'new.txt').write_bytes(b'new contents')
        with patch.object(audit, 'capture_synced_folder', side_effect=engine.BackupError('synthetic outage')):
            with self.assertRaises(engine.BackupError):
                self.manager.sync()
        expected = self.summary()
        self.assertEqual(expected['cloudBytes'], 15)
        self.manager = engine.BackupManager(self.base, self.cloud)
        restored = self.summary()
        self.assertEqual(restored['cloudBytes'], 15)
        self.assertEqual(restored['localTotalBytes'], 15)
        self.assertEqual(restored['measuredAt'], expected['measuredAt'])
        self.assertEqual(self.manager.public()['syncMatch']['percent'], 100)
        self.manager.sync()
        self.assertGreater(self.summary()['measuredAt'], restored['measuredAt'])
        self.assertNotIn('inProgress', self.summary())

    def test_new_audit_replaces_live_observation_left_by_failed_sync(self):
        (self.source / 'old.txt').write_bytes(b'old')
        with patch.object(audit, 'capture_synced_folder', side_effect=engine.BackupError('synthetic outage')):
            with self.assertRaises(engine.BackupError):
                self.manager.sync()
        self.assertTrue(self.summary()['inProgress'])
        (self.source / 'missing.txt').write_bytes(b'missing')
        audit.audit_folders(self.manager)
        self.assertEqual(self.summary()['localTotalBytes'], 10)
        self.assertEqual(self.summary()['cloudBytes'], 3)
        self.assertNotIn('inProgress', self.summary())

    def test_changed_remote_root_invalidates_volume_but_schedule_does_not(self):
        (self.source / 'hello.txt').write_bytes(b'hello')
        self.manager.sync()
        changed = self.manager.config | {'folders': [self.job | {'syncMode': 'daily'}]}
        self.manager.save_config(changed)
        self.assertEqual(self.manager.public()['syncMatch']['percent'], 100)
        self.manager.save_config(self.manager.config | {'remoteRoot': 'Other destination'})
        self.assertIsNone(self.manager.public()['syncMatch']['percent'])

    def test_read_only_missing_files_and_actual_sizes(self):
        (self.source / 'missing.txt').write_bytes(b'hello')
        (self.source / '.env').write_bytes(b'private')
        audit.audit_folders(self.manager)
        self.assertFalse(self.manager.state['lastResult']['ok'])
        self.assertEqual(self.cloud.objects, {})
        summary = self.summary()
        self.assertEqual(summary['localBytes'], 5)
        self.assertEqual(summary['localTotalBytes'], 12)
        self.assertEqual(summary['cloudBytes'], 0)
        self.assertEqual(summary['issues'], 1)
        self.assertEqual(summary['excluded'], 1)
        files = self.manager.handle('folder-files', {'job': self.job['id']})
        self.assertEqual(files['items'][0]['localBytes'], 5)
        self.assertIsNone(files['items'][0]['cloudBytes'])
        self.assertIn('موجود نیست', files['items'][0]['error'])

    def test_full_sync_publishes_sizes_without_repeating_content_downloads(self):
        (self.source / 'hello.txt').write_bytes(b'hello')
        (self.source / '.env').write_bytes(b'private')
        self.manager.sync()
        summary = self.summary()
        self.assertEqual(summary['localTotalBytes'], 12)
        self.assertEqual(summary['localBytes'], 5)
        self.assertEqual(summary['cloudBytes'], 5)
        self.assertTrue(summary['complete'])
        self.assertEqual(summary['excluded'], 1)
        self.assertEqual(self.cloud.verifications, 1)

    def test_audit_repairs_only_recorded_missing_files_then_verifies_everything(self):
        (self.source / 'missing.txt').write_bytes(b'hello')
        audit.audit_folders(self.manager)
        self.manager.handle('repair-audit', {})
        self.assertTrue(self.manager.work_lock.acquire(timeout=10))
        self.manager.work_lock.release()
        self.assertEqual(self.manager.state['lastResult']['files'], 1)
        self.assertTrue(self.manager.state['lastResult']['partial'])
        audit.audit_folders(self.manager)
        self.assertTrue(self.summary()['complete'])
        with self.assertRaises(engine.BackupError):
            self.manager.handle('repair-audit', {})

    def test_recursive_listing_and_bounded_parallel_downloads(self):
        for number in range(8):
            (self.source / f'{number}.txt').write_bytes(b'hello')
        self.manager.sync()
        remote_root = next(item['id'] for item in self.cloud.objects.values() if item['name'] == self.manager.config['remoteRoot'])
        parent = next(item['id'] for item in self.cloud.objects.values() if item['parent'] == remote_root and item['name'] == self.job['name'])
        self.cloud.list_tree = lambda folder: [item for item in self.cloud.objects.values() if item['parent'] == folder]
        files, errors = audit.remote_tree(self.manager, parent)
        self.assertEqual(len(files), 8)
        self.assertEqual(errors, [])
        barrier = threading.Barrier(4)
        verify = self.cloud.verify
        def concurrent_verify(*args, **kwargs):
            barrier.wait(timeout=5)
            verify(*args, **kwargs)
        with patch.object(self.cloud, 'verify', side_effect=concurrent_verify):
            audit.audit_folders(self.manager)
        self.assertTrue(self.summary()['complete'])

    def test_successful_audit_acknowledges_pending_generation(self):
        (self.source / 'hello.txt').write_bytes(b'hello')
        self.manager.sync()
        self.manager.config['automatic'] = True
        self.manager._queue_sync()
        self.manager.start('audit')
        self.assertTrue(self.manager.work_lock.acquire(timeout=10))
        self.manager.work_lock.release()
        self.assertFalse(self.manager.public()['state']['pendingSync'])
        self.assertFalse(self.summary()['stale'])

    def test_slow_download_does_not_block_the_remaining_queue(self):
        for number in range(12):
            (self.source / f'{number:02}.txt').write_bytes(b'hello')
        self.manager.sync()
        release = threading.Event()
        lock = threading.Lock()
        completed = 0
        verify = self.cloud.verify
        def delayed_verify(identifier, *args, **kwargs):
            nonlocal completed
            if self.cloud.objects[identifier]['name'] == '00.txt':
                self.assertTrue(release.wait(8), 'A slow download blocked every later batch')
            else:
                with lock:
                    completed += 1
                    if completed >= 8:
                        release.set()
            return verify(identifier, *args, **kwargs)
        with patch.object(self.cloud, 'verify', side_effect=delayed_verify):
            audit.audit_folders(self.manager)
        self.assertTrue(release.is_set())
        self.assertTrue(self.summary()['complete'])

    def test_verified_sizes_extras_and_stale_evidence(self):
        path = self.source / 'hello.txt'
        path.write_bytes(b'hello')
        self.manager.sync()
        original_uploads = self.cloud.uploads
        audit.audit_folders(self.manager)
        self.assertEqual(self.cloud.uploads, original_uploads)
        self.assertTrue(self.summary()['complete'])
        self.assertFalse(self.summary()['stale'])
        self.assertEqual(self.summary()['verifiedBytes'], 5)
        self.assertEqual(self.summary()['cloudBytes'], 5)
        with self.manager.db() as database:
            database.execute('DELETE FROM run_items')
        files = self.manager.handle('folder-files', {'job': self.job['id']})
        selected = self.manager.selected_item({'run': files['run'], 'path': str(path)})
        self.assertEqual(selected, path.resolve())
        self.manager.sync(only_paths={str(path.resolve())})
        self.assertTrue(self.summary()['stale'])
        path.unlink()
        audit.audit_folders(self.manager)
        self.assertEqual(self.summary()['cloudExtras'], 1)
        self.assertEqual(self.summary()['cloudBytes'], 5)
        self.assertEqual(self.summary()['localBytes'], 0)
        self.assertTrue(self.summary()['complete'])

    def test_hash_mismatch_is_not_hidden_by_equal_sizes(self):
        (self.source / 'hello.txt').write_bytes(b'hello')
        self.manager.sync()
        remote = next(item for item in self.cloud.objects.values() if item.get('data') == b'hello')
        remote['data'] = b'wrong'
        audit.audit_folders(self.manager)
        self.assertEqual(self.summary()['issues'], 1)
        self.assertEqual(self.summary()['verifiedFiles'], 0)
        self.assertFalse(self.summary()['complete'])
        self.assertFalse(self.manager.state['lastResult']['ok'])

    def revision_file(self):
        (self.source / 'hello.txt').write_bytes(b'hello')
        self.manager.sync()
        remote = next(item for item in self.cloud.objects.values() if item.get('data') == b'hello')
        remote.update(etag='opaque-revision-one', version=1, last_modified=123)
        return remote

    def test_only_same_verified_revision_and_local_content_can_reuse_proof(self):
        remote = self.revision_file()
        audit.audit_folders(self.manager)
        downloaded = self.cloud.verifications
        first_check = self.summary()['contentVerifiedAt']
        audit.audit_folders(self.manager)
        self.assertEqual(self.cloud.verifications, downloaded)
        self.assertEqual(self.manager.state['lastResult']['reusedProofs'], 1)
        self.assertEqual(self.summary()['contentVerifiedAt'], first_check)
        (self.source / 'hello.txt').write_bytes(b'wrong')
        audit.audit_folders(self.manager)
        self.assertEqual(self.cloud.verifications, downloaded + 1)
        self.assertFalse(self.summary()['complete'])
        (self.source / 'hello.txt').write_bytes(b'hello')
        remote.update(data=b'wrong', etag='opaque-revision-two')
        audit.audit_folders(self.manager)
        self.assertEqual(self.cloud.verifications, downloaded + 2)
        self.assertFalse(self.summary()['complete'])

    def test_expired_or_missing_revision_and_deep_audit_require_download(self):
        remote = self.revision_file()
        audit.audit_folders(self.manager)
        downloaded = self.cloud.verifications
        audit.audit_folders(self.manager, reuse=False)
        self.assertEqual(self.cloud.verifications, downloaded + 1)
        self.assertEqual(self.manager.state['lastResult']['reusedProofs'], 0)
        with self.manager.db() as database:
            database.execute('UPDATE verified_objects SET verified_at=0')
        audit.audit_folders(self.manager)
        self.assertEqual(self.cloud.verifications, downloaded + 2)
        remote.pop('etag')
        audit.audit_folders(self.manager)
        self.assertEqual(self.cloud.verifications, downloaded + 3)
        with self.assertRaises(engine.BackupError):
            self.manager.handle('audit', {'deep': 'false'})

    def test_failed_deep_check_invalidates_a_previously_cached_proof(self):
        remote = self.revision_file()
        audit.audit_folders(self.manager)
        remote['data'] = b'wrong'
        audit.audit_folders(self.manager, reuse=False)
        self.assertFalse(self.summary()['complete'])
        audit.audit_folders(self.manager)
        self.assertFalse(self.summary()['complete'])
        self.assertEqual(self.manager.state['lastResult']['reusedProofs'], 0)

    def test_full_sync_reuses_only_current_content_proofs(self):
        self.revision_file()
        audit.audit_folders(self.manager)
        downloaded = self.cloud.verifications
        self.manager.sync()
        self.assertTrue(self.summary()['complete'])
        self.assertIsNotNone(self.summary()['contentVerifiedAt'])
        self.assertEqual(self.cloud.verifications, downloaded)

    def test_new_process_and_changed_config_invalidate_old_results(self):
        (self.source / 'hello.txt').write_bytes(b'hello')
        self.manager.sync()
        audit.audit_folders(self.manager)
        self.manager.save_config(self.manager.config | {'exclude': ['*.txt']})
        self.assertTrue(self.summary()['stale'])
        other = engine.BackupManager(self.base, self.cloud)
        self.assertTrue(other.public()['folderStats'][self.job['id']]['stale'])

    def test_duplicate_names_and_invalid_remote_paths_are_rejected(self):
        self.cloud.objects[1] = {'id': 1, 'name': 'same', 'parent': 7, 'key': 'key', 'size': 2}
        self.cloud.objects[2] = {'id': 2, 'name': 'same', 'parent': 7, 'key': 'key', 'size': 2}
        files, errors = audit.remote_tree(self.manager, 7)
        self.assertEqual(files, {})
        self.assertEqual(len(errors), 1)
        self.cloud.objects[3] = {'id': 3, 'name': '../private', 'parent': 7, 'key': 'key', 'size': 2}
        with self.assertRaises(engine.BackupError):
            audit.remote_tree(self.manager, 7)

    def test_file_size_survives_failed_snapshot(self):
        path = self.source / 'hello.txt'
        path.write_bytes(b'hello')
        with patch.object(self.manager, 'snapshot', side_effect=engine.BackupError('synthetic')):
            self.manager.sync()
        result = self.manager.items({'run': self.manager.state['lastResult']['id']})
        self.assertEqual(result['items'][0]['bytes'], 5)

    def test_completed_empty_folder_is_not_reported_as_missing(self):
        audit.audit_folders(self.manager)
        self.assertTrue(self.summary()['complete'])
        self.assertEqual(self.summary()['localFiles'], 0)

    def test_folder_files_validation(self):
        for request in [{'job': 'unknown'}, {'job': self.job['id'], 'offset': -1},
                        {'job': self.job['id'], 'offset': True}]:
            with self.assertRaises(engine.BackupError):
                self.manager.handle('folder-files', request)

    def test_batched_status_and_proof_commit_is_atomic(self):
        path = self.source / 'hello.txt'
        path.write_bytes(b'hello')
        run = 'a' * 32
        self.manager.record_item(run, path, 'queued', 5)
        row = (self.job['id'], 'hello.txt', 5, 5, 1, 'unchanged', '')
        with self.assertRaises(sqlite3.Error):
            audit.store_checked(self.manager, run, self.source, [(row, ('invalid-proof',))])
        with self.manager.db() as database:
            self.assertEqual(database.execute('SELECT status FROM run_items WHERE run=?', (run,)).fetchone()[0], 'queued')
            self.assertEqual(database.execute('SELECT count(*) FROM files').fetchone()[0], 0)

    def test_multiple_verification_batches_keep_errors_and_all_file_rows(self):
        for number in range(65):
            (self.source / f'{number:02}.txt').write_bytes(b'hello')
        self.manager.sync()
        remote = next(item for item in self.cloud.objects.values() if item['name'] == '32.txt')
        remote['data'] = b'wrong'
        audit.audit_folders(self.manager)
        self.assertEqual(self.summary()['verifiedFiles'], 64)
        self.assertEqual(self.summary()['issues'], 1)
        with self.manager.db() as database:
            counts = dict(database.execute('SELECT status,count(*) FROM run_items WHERE run=? GROUP BY status',
                                           (self.manager.state['lastResult']['id'],)))
        self.assertEqual(counts, {'error': 1, 'unchanged': 64})

    def test_volume_ratio_keeps_last_observation_when_content_evidence_is_stale(self):
        (self.source / 'hello.txt').write_bytes(b'hello')
        self.manager.sync()
        match = self.manager.public()['syncMatch']
        self.assertEqual(match['percent'], 100)
        self.assertTrue(match['complete'])
        self.manager._queue_sync()
        match = self.manager.public()['syncMatch']
        self.assertEqual(match['percent'], 100)
        self.assertTrue(match['stale'])
        audit.audit_folders(self.manager)
        self.manager.synced_generation = self.manager.change_generation
        match = self.manager.public()['syncMatch']
        self.assertEqual(match['percent'], 100)
        (self.source / 'new.txt').write_bytes(b'missing')
        audit.audit_folders(self.manager)
        match = self.manager.public()['syncMatch']
        self.assertAlmostEqual(match['percent'], 100 * 5 / 12)
        self.assertEqual(match['totalBytes'], 12)
        self.assertEqual(match['remainingBytes'], 7)
        self.assertFalse(match['complete'])

    def test_recursive_full_sync_does_not_list_each_nested_folder(self):
        for number in range(25):
            directory = self.source / str(number)
            directory.mkdir()
            (directory / 'file.txt').write_bytes(b'hello')
        self.manager.sync()
        def subtree(parent):
            owners, found = [parent], []
            while owners:
                owner = owners.pop()
                children = [dict(item) for item in self.cloud.objects.values() if item['parent'] == owner]
                found.extend(children)
                owners.extend(item['id'] for item in children if not item.get('key'))
            return found
        self.cloud.list_tree = subtree
        with patch.object(self.cloud, 'list_folder', wraps=self.cloud.list_folder) as listing:
            self.manager.sync()
        self.assertTrue(self.summary()['complete'])
        self.assertLess(listing.call_count, 15)

    def test_download_retry_refreshes_signed_url_but_not_integrity_failure(self):
        client = engine.HamrahiClient(self.base / 'client')
        with patch.object(client, '_verify_once', side_effect=[engine.BackupError('temporary', 502), None]) as verify:
            with patch.object(engine.time, 'sleep'):
                client.verify(1, 'synthetic', 5)
        self.assertEqual(verify.call_count, 2)
        with patch.object(client, '_verify_once', side_effect=engine.BackupError('hash mismatch')) as verify:
            with self.assertRaises(engine.BackupError):
                client.verify(1, 'synthetic', 5)
        self.assertEqual(verify.call_count, 1)


if __name__ == '__main__':
    unittest.main()
