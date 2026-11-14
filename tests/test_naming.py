"""Exact names and recovery at the destructive-operation boundary (fake cloud)."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from hamrahi_backup import engine as cb
from test_engine import FakeCloud


class NamingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.cloud = FakeCloud()
        self.manager = cb.BackupManager(self.base, self.cloud)
        self.manager.save_config(self.manager.config | {'syncBehavior': 'versioned', 'settleSeconds': 0,
                                 'folders': [{'path': str(self.source)}]})
        self.job = self.manager.config['folders'][0]

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def data(self):
        return [o for o in self.cloud.objects.values() if o.get('key') and not o['name'].startswith('_manifest-')]

    def test_version_lookup_does_not_create_empty_history_for_new_content(self):
        path = self.source / 'hello.txt'
        path.write_bytes(b'before')
        self.manager.sync()
        path.write_bytes(b'after')
        self.manager.sync()
        self.assertTrue(self.manager.state['lastResult']['ok'])
        digest = hashlib.sha256(b'after').hexdigest()
        self.assertFalse(any(item['name'] == digest for item in self.cloud.objects.values()))

    def test_exact_unicode_long_and_punctuation_names(self):
        names = ['گزارش + #1 [نسخه].txt', 'a' * 170 + '.txt', 'archive.tar.gz']
        for name in names:
            (self.source / name).write_text(name, encoding='utf8')
        self.manager.sync()
        self.assertTrue(self.manager.state['lastResult']['ok'])
        self.assertEqual({o['name'] for o in self.data()}, set(names))
        before = self.cloud.uploads
        self.manager.sync()
        self.assertEqual(self.cloud.uploads, before, 'unchanged sync must not upload even a manifest')

    def test_update_archives_old_bytes_and_revert_reuses_existing_object(self):
        file = self.source / 'same.txt'
        file.write_bytes(b'one')
        self.manager.sync()
        original = self.data()[0]
        original_id, parent = original['id'], original['parent']
        file.write_bytes(b'two')
        self.manager.sync()
        self.assertNotEqual(original['parent'], parent)
        self.assertEqual(original['data'], b'one')
        current = next(x for x in self.cloud.list_folder(parent) if x['name'] == file.name)
        self.assertEqual(current['data'], b'two')
        file.write_bytes(b'one')
        self.manager.sync()
        self.assertEqual(len(self.data()), 2)
        self.assertEqual(self.cloud.list_folder(parent)[0]['id'], original_id)

    def test_failed_upload_after_archive_recovers_without_duplicate(self):
        file = self.source / 'same.txt'
        file.write_bytes(b'one')
        self.manager.sync()
        file.write_bytes(b'two')
        upload = self.cloud.upload
        def fail(*a, **kw):
            if a[1] == file.name:
                raise cb.BackupError('synthetic transfer failure')
            return upload(*a, **kw)
        self.cloud.upload = fail
        self.manager.sync()
        self.assertFalse(self.manager.state['lastResult']['ok'])
        self.assertEqual([o['data'] for o in self.data()], [b'one'])
        self.cloud.upload = upload
        self.manager.sync()
        self.assertTrue(self.manager.state['lastResult']['ok'])
        self.assertEqual({o['data'] for o in self.data()}, {b'one', b'two'})

    def test_unmanaged_same_name_is_never_moved_or_overwritten(self):
        file = self.source / 'same.txt'
        file.write_bytes(b'one')
        self.manager.sync()
        original = self.data()[0]
        parent = original['parent']
        with self.manager.db() as db:
            db.execute('DELETE FROM files')
        file.write_bytes(b'two')
        self.manager.sync()
        self.assertFalse(self.manager.state['lastResult']['ok'])
        self.assertEqual(original['parent'], parent)
        self.assertEqual(len(self.data()), 1)

    def test_legacy_tree_migrates_in_place_without_uploading_data(self):
        nested = self.source / 'child + #1'
        nested.mkdir()
        file = nested / 'گزارش + #1.txt'
        file.write_bytes(b'original')
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        root = self.cloud.folder(self.manager.config['remoteRoot'], None, {})
        job_parent = self.cloud.folder(self.job['name'] + '-' + self.job['id'], root, {})
        nested_parent = self.cloud.folder(cb.cloud_name(nested.name) + '-' + hashlib.sha256(nested.name.encode()).hexdigest()[:8], job_parent, {})
        old_name = cb.cloud_name(file.stem)[:120] + '__' + digest[:20] + file.suffix
        obj = self.cloud.upload(file, old_name, nested_parent)
        with self.manager.db() as db:
            db.execute('INSERT INTO files VALUES (?,?,?,?,?,?,?)', (self.job['id'], file.relative_to(self.source).as_posix(), digest, file.stat().st_size, 0, obj['id'], old_name))
        self.manager.sync()
        self.assertTrue(self.manager.state['lastResult']['ok'])
        self.assertEqual(len(self.data()), 1)
        self.assertEqual(obj['name'], file.name)
        self.assertEqual(self.cloud.objects[job_parent]['name'], self.source.name)
        self.assertEqual(self.cloud.objects[nested_parent]['name'], nested.name)
        self.assertEqual(self.manager.state['lastResult']['uploaded'], 0)

    def test_legacy_and_new_folders_conflict_without_merging(self):
        (self.source / 'file.txt').write_bytes(b'one')
        root = self.cloud.folder(self.manager.config['remoteRoot'], None, {})
        self.cloud.folder(self.job['name'], root, {})
        self.cloud.folder(self.job['name'] + '-' + self.job['id'], root, {})
        with self.assertRaises(cb.BackupError):
            self.manager.sync()
        self.assertEqual(self.data(), [])

    def test_identical_files_with_different_names_are_preserved(self):
        for name in ('report.txt', 'report - Copy.txt'):
            (self.source / name).write_bytes(b'same content')
        self.manager.sync()
        self.assertEqual({o['name'] for o in self.data()}, {'report.txt', 'report - Copy.txt'})

    def test_corrupt_predecessor_is_never_archived_or_replaced(self):
        file = self.source / 'same.txt'
        file.write_bytes(b'one')
        self.manager.sync()
        original = self.data()[0]
        parent = original['parent']
        original['data'] = b'bad'
        file.write_bytes(b'two')
        self.manager.sync()
        self.assertFalse(self.manager.state['lastResult']['ok'])
        self.assertEqual(original['parent'], parent)
        self.assertEqual(len(self.data()), 1)

    def test_reserved_source_name_is_rejected(self):
        reserved = self.base / cb.METADATA
        reserved.mkdir()
        with self.assertRaises(cb.BackupError):
            self.manager.save_config(self.manager.config | {'folders': [{'path': str(reserved)}]})


class RelocationTests(unittest.TestCase):
    def test_provider_renaming_is_not_recorded_as_a_successful_upload(self):
        from unittest.mock import patch
        from test_engine import Response
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            file = base / 'original + #1.txt'
            file.write_bytes(b'one')
            client = cb.HamrahiClient(base)
            def api(method, endpoint, payload=None):
                if endpoint.endswith('start-upload/'):
                    return {'signed_urls': ['https://storage.example/x'], 'chunk_size': 3,
                            'key': 'k', 'upload_id': 'u'}
                return {'id': 1, 'size': 3, 'name': 'original___1.txt', 'parent': 7}
            client._json = api
            client.opener.open = lambda *a, **k: Response(headers={'ETag': 'receipt'})
            with patch.object(cb, 'check_transfer_url'), self.assertRaises(cb.BackupError):
                client.upload(file, file.name, 7)

    def test_ambiguous_move_is_read_back_without_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            client = cb.HamrahiClient(Path(temp))
            obj = {'id': 10, 'parent': 1, 'name': 'original.txt', 'size': 3, 'key': 'k'}
            mutations = []
            def api(method, endpoint, payload=None):
                if method == 'POST':
                    mutations.append(payload)
                    obj['parent'] = 2
                    raise cb.BackupError('synthetic lost response', 502)
                return dict(obj)
            client._json = api
            client.move(10, 2)
            self.assertEqual(len(mutations), 1)
            self.assertEqual(mutations[0], {'source_obj_id': 10, 'target_parent_id': 2})

    def test_failed_move_does_not_report_success_or_repeat(self):
        with tempfile.TemporaryDirectory() as temp:
            client = cb.HamrahiClient(Path(temp))
            obj = {'id': 10, 'parent': 1, 'name': 'original.txt', 'size': 3, 'key': 'k'}
            mutations = []
            def api(method, endpoint, payload=None):
                if method == 'POST':
                    mutations.append(payload)
                    raise cb.BackupError('synthetic lost response', 502)
                return dict(obj)
            client._json = api
            with self.assertRaises(cb.BackupError):
                client.move(10, 2)
            self.assertEqual(len(mutations), 1)

    def test_rename_cannot_change_object_content_or_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            client = cb.HamrahiClient(Path(temp))
            obj = {'id': 10, 'parent': 1, 'name': 'old.txt', 'size': 3, 'key': 'k'}
            def api(method, endpoint, payload=None):
                if method == 'POST':
                    obj.update(name='new.txt', key='different-content')
                    return {}
                return dict(obj)
            client._json = api
            with self.assertRaises(cb.BackupError):
                client.rename(10, 'new.txt')
