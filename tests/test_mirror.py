from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hamrahi_backup.engine import BackupError, BackupManager, HamrahiClient
from test_engine import FakeCloud


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / 'Original'
        self.source.mkdir()
        self.cloud = FakeCloud()
        self.manager = BackupManager(self.base, self.cloud)
        self.manager.save_config(self.manager.config | {'settleSeconds': 0, 'verifyUploads': False,
            'folders': [{'path': str(self.source)}]})
        self.job = self.manager.config['folders'][0]

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def test_replacement_keeps_one_original_path_and_unchanged_is_not_uploaded(self):
        source = self.source / 'گزارش.txt'
        source.write_text('first', encoding='utf-8')
        self.manager.sync()
        original = next(item for item in self.cloud.objects.values() if item.get('key'))
        self.manager.sync()
        self.assertEqual(self.cloud.uploads, 1)
        source.write_text('second revision', encoding='utf-8')
        self.manager.sync()
        files = [item for item in self.cloud.objects.values() if item.get('key')]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['name'], source.name)
        self.assertEqual(files[0]['parent'], original['parent'])
        self.assertEqual(files[0]['data'], source.read_bytes())
        self.assertIn(original['id'], self.cloud.native_versions)
        self.assertFalse(any(item['name'] in ('history', 'manifests') for item in self.cloud.objects.values()))

    def test_removed_files_and_empty_directories_use_native_trash(self):
        nested = self.source / 'nested'
        nested.mkdir()
        source = nested / 'file.txt'
        source.write_bytes(b'payload')
        self.manager.sync()
        source.unlink()
        nested.rmdir()
        self.manager.sync()
        self.assertEqual({item['name'] for item in self.cloud.trashed.values()}, {'file.txt', 'nested'})
        self.assertTrue(all(item['is_trash'] for item in self.cloud.trashed.values()))
        self.assertEqual(self.manager.state['lastResult']['trashed'], 2)

    def test_missing_source_root_never_trashes_cloud(self):
        source = self.source / 'file.txt'
        source.write_bytes(b'payload')
        self.manager.sync()
        source.unlink()
        self.source.rmdir()
        self.manager.sync()
        self.assertFalse(self.cloud.trashed)
        self.assertTrue(self.manager.state['lastResult']['errors'])

    def test_partial_retry_does_not_delete_other_files(self):
        first = self.source / 'first.txt'
        second = self.source / 'second.txt'
        first.write_bytes(b'first')
        second.write_bytes(b'second')
        self.manager.sync()
        second.unlink()
        self.manager.sync(only_paths={str(first.resolve())})
        self.assertFalse(self.cloud.trashed)

    def test_excluded_cloud_file_is_protected(self):
        source = self.source / 'keep.txt'
        source.write_bytes(b'payload')
        self.manager.sync()
        source.unlink()
        self.manager.save_config(self.manager.config | {'exclude': ['keep.txt']})
        self.manager.sync()
        self.assertFalse(self.cloud.trashed)

    def test_replaced_local_root_during_upload_stops_cleanup(self):
        source = self.source / 'file.txt'
        source.write_bytes(b'payload')
        upload = self.cloud.upload
        def replace_root(*args, **kwargs):
            result = upload(*args, **kwargs)
            self.source.rename(self.base / 'original-moved')
            self.source.mkdir()
            return result
        with patch.object(self.cloud, 'upload', side_effect=replace_root):
            self.manager.sync()
        self.assertFalse(self.cloud.trashed)
        self.assertTrue(self.manager.state['lastResult']['errors'])

    def test_new_cloud_child_prevents_trashing_its_parent_directory(self):
        nested = self.source / 'nested'
        nested.mkdir()
        source = nested / 'file.txt'
        source.write_bytes(b'payload')
        self.manager.sync()
        parent = next(item['id'] for item in self.cloud.objects.values() if item['name'] == 'nested')
        source.unlink()
        nested.rmdir()
        trash = self.cloud.trash
        def concurrent_addition(expected):
            result = trash(expected)
            if expected['name'] == 'file.txt':
                identifier = self.cloud.next_id
                self.cloud.next_id += 1
                self.cloud.objects[identifier] = {'id': identifier, 'name': 'late.txt', 'parent': parent,
                    'key': 'late-key', 'size': 4, 'data': b'late'}
            return result
        with patch.object(self.cloud, 'trash', side_effect=concurrent_addition):
            self.manager.sync()
        self.assertIn(parent, self.cloud.objects)
        self.assertNotIn(parent, self.cloud.trashed)

    def test_schedule_can_be_saved_while_upload_lock_is_held(self):
        with self.manager.work_lock:
            self.manager.state['busy'] = True
            self.manager.handle('folder-schedule', {'job': self.job['id'], 'syncMode': 'daily', 'dailyTime': '04:20'})
        self.assertEqual(self.manager.config['folders'][0]['syncMode'], 'daily')
        self.assertEqual(self.manager.config['folders'][0]['dailyTime'], '04:20')
        self.assertTrue(self.manager.state['busy'])
        with self.assertRaises(BackupError):
            self.manager.handle('folder-schedule', {'job': self.job['id'], 'syncMode': 'daily', 'dailyTime': ''})

    def test_native_trash_reconciles_ambiguous_response_without_replay(self):
        expected = {'id': 7, 'name': 'file.txt', 'parent': 3, 'key': 'key', 'size': 4}
        client = object.__new__(HamrahiClient)
        with patch.object(client, '_json', side_effect=[expected, BackupError('HTTP 500', 500),
                                                       expected | {'is_trash': True}]) as request:
            client.trash(expected)
        self.assertEqual([call.args[0] for call in request.call_args_list], ['GET', 'DELETE', 'GET'])
        self.assertEqual(request.call_args_list[1].args[1], 'v2/rgw/trash-objects/')

    def test_changed_cloud_target_is_never_trashed(self):
        expected = {'id': 7, 'name': 'file.txt', 'parent': 3, 'key': 'key', 'size': 4}
        client = object.__new__(HamrahiClient)
        with patch.object(client, '_json', return_value=expected | {'size': 5}) as request:
            with self.assertRaises(BackupError):
                client.trash(expected)
        self.assertEqual(request.call_count, 1)
