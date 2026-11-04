"""Behavioral regression tests. All transfers use in-memory fake storage."""
import hashlib
from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from hamrahi_backup import engine as cb


class FakeCloud:
    def __init__(self):
        self.tokens = {'access': 'test-access', 'refresh': 'test-refresh'}
        self.session_error = None
        self.objects = {}
        self.next_id = 1
        self.uploads = 0
        self.verifications = 0
        self.fail_verify = False

    def connection(self):
        return {'checkedAt': cb.now(), 'used': 0, 'total': 100000000}

    def list_folder(self, parent=None):
        return [dict(x) for x in self.objects.values() if x['parent'] == parent]

    def folder(self, name, parent, cache):
        for item in self.list_folder(parent):
            if item['name'] == name and not item.get('key'):
                return item['id']
        number = self.next_id
        self.next_id += 1
        self.objects[number] = {'id': number, 'name': name, 'parent': parent, 'key': None}
        return number

    def upload(self, path, name, parent, progress=lambda x: None, **kwargs):
        self.uploads += 1
        number = self.next_id
        self.next_id += 1
        data = path.read_bytes()
        obj = {'id': number, 'name': name, 'parent': parent, 'size': len(data), 'key': 'object', 'data': data}
        self.objects[number] = obj
        return obj

    def verify(self, number, digest, size, **kwargs):
        self.verifications += 1
        if self.fail_verify:
            raise cb.BackupError('simulated integrity failure')
        data = self.objects[number]['data']
        if hashlib.sha256(data).hexdigest() != digest or len(data) != size:
            raise cb.BackupError('hash mismatch')


class Response:
    def __init__(self, data=b'', headers=None):
        self.data = io.BytesIO(data)
        self.headers = headers or {}

    def read(self, amount=-1):
        return self.data.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.backup-test-')
        self.base = Path(self.temp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.fake = FakeCloud()
        self.engine = cb.BackupManager(self.base, self.fake)
        config = self.engine.public()['config']
        config.update(settleSeconds=0, folders=[{'path': str(self.source), 'name': 'Documents'}])
        self.engine.save_config(config)

    def tearDown(self):
        self.temp.cleanup()

    def files(self):
        return [x for x in self.fake.objects.values() if x.get('key') and not x['name'].startswith('_manifest')]

    def test_preview_never_calls_cloud_and_has_no_index_writes(self):
        (self.source / 'a.txt').write_text('hello')
        self.fake.connection = lambda: self.fail('preview contacted cloud')
        self.engine.sync(preview=True)
        self.assertEqual(self.fake.uploads, 0)
        self.assertEqual(self.engine.state['lastResult']['uploaded'], 1)
        with self.engine.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM files').fetchone()[0], 0)

    def test_incremental_hashes_preserve_versions_and_cloud_deletions_heal(self):
        file = self.source / 'hello.txt'
        file.write_text('one')
        self.engine.sync()
        first = self.files()[0]
        self.assertTrue(first['name'].endswith('.txt'))
        self.engine.sync()
        self.assertEqual(len(self.files()), 1)
        self.assertEqual(self.engine.state['lastResult']['unchanged'], 1)
        old_stat = file.stat()
        file.write_text('two')
        os.utime(file, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        self.engine.sync()
        self.assertEqual(len(self.files()), 2, 'same size and mtime still must detect hash changes')
        current = next(x for x in self.files() if x['data'] == b'two')
        del self.fake.objects[current['id']]
        self.engine.sync()
        self.assertEqual(len(self.files()), 2, 'deleted cloud copy should be uploaded again')
        file.unlink()
        self.engine.sync()
        self.assertEqual(len(self.files()), 2, 'local deletion must never remove cloud history')
        manifests = [x for x in self.fake.objects.values() if x['name'].startswith('_manifest')]
        self.assertEqual(json.loads(manifests[-1]['data'])['files'], [])

    def test_exclusions_and_backup_credentials_never_leave_machine(self):
        (self.source / '.env').write_text('secret')
        (self.source / 'key.pem').write_text('private-key')
        (self.source / 'node_modules').mkdir()
        (self.source / 'node_modules' / 'huge.js').write_text('skip')
        (self.source / 'safe.txt').write_text('public')
        self.engine.sync()
        self.assertEqual([x['data'] for x in self.files()], [b'public'])
        config = self.engine.public()['config']
        config['folders'] = [{'path': str(self.base), 'name': 'All'}]
        config['exclude'] = []
        self.engine.save_config(config)
        (self.engine.state_dir / 'secret.bin').write_bytes(b'NEVER-COPY-THIS')
        self.engine.sync(preview=True)
        self.assertFalse(any(str(self.engine.state_dir) in x['path'] for x in self.engine.state['lastResult']['samples']))

    def test_failed_verification_is_not_recorded_as_success(self):
        (self.source / 'a.txt').write_text('hello')
        self.fake.fail_verify = True
        with self.assertRaises(cb.BackupError):
            self.engine.sync()
        with self.engine.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM files').fetchone()[0], 0)
        self.fake.fail_verify = False
        self.engine.sync()
        self.assertEqual(len(self.files()), 1, 'ambiguous success should be adopted only after hash verification')
        self.assertTrue(self.engine.state['lastResult']['ok'])

    def test_sqlite_snapshot_includes_committed_wal_without_changing_source(self):
        file = self.source / 'live.db'
        with closing(sqlite3.connect(file)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE notes (value TEXT)')
            db.commit()
            db.execute("INSERT INTO notes VALUES ('committed in WAL')")
            db.commit()
            db.execute("INSERT INTO notes VALUES ('uncommitted')")
            target = self.base / 'snapshot.sqlite'
            digest, size, mtime, is_sqlite = self.engine.snapshot(file, target)
            self.assertTrue(is_sqlite)
            with closing(sqlite3.connect(target)) as snapshot:
                self.assertEqual(snapshot.execute('SELECT value FROM notes').fetchall(), [('committed in WAL',)])
            self.assertEqual(digest, hashlib.sha256(target.read_bytes()).hexdigest())
            db.rollback()

    def test_missing_source_is_partial_failure_and_never_success(self):
        self.source.rmdir()
        self.engine.sync(preview=True)
        self.assertFalse(self.engine.state['lastResult']['ok'])
        self.assertTrue(self.engine.state['lastResult']['errors'])

    def test_limits_recent_files_and_live_sql_server_are_reported(self):
        (self.source / 'live.mdf').write_bytes(b'database')
        config = self.engine.public()['config']
        config['maxFileMiB'] = 1
        self.engine.save_config(config)
        (self.source / 'large.bin').write_bytes(b'x' * (1048576 + 1))
        self.engine.sync(preview=True)
        self.assertEqual(len(self.engine.state['lastResult']['errors']), 2)
        (self.source / 'live.mdf').unlink()
        (self.source / 'large.bin').unlink()
        (self.source / 'new.txt').write_text('recent')
        config['settleSeconds'] = 3600
        self.engine.save_config(config)
        self.engine.sync(preview=True)
        self.assertFalse(self.engine.state['lastResult']['ok'])
        self.assertEqual(self.engine.state['lastResult']['files'], 0)

    def test_config_rejects_overlap_root_duplicate_id_and_unsafe_state(self):
        config = self.engine.public()['config']
        (self.source / 'child').mkdir()
        for folders in [[{'path': str(self.source)}, {'path': str(self.source / 'child')}],
                        [{'path': self.source.anchor}], [{'path': str(self.engine.state_dir)}],
                        [{'path': str(self.source), 'id': '../bad'}]]:
            with self.subTest(folders=folders):
                with self.assertRaises(cb.BackupError):
                    self.engine.save_config(config | {'folders': folders})

    def test_corrupted_config_disables_scheduler(self):
        self.engine.config_path.write_text('{broken')
        replacement = cb.BackupManager(self.base, self.fake)
        self.assertIsNotNone(replacement.config_error)
        self.assertFalse(replacement.config['automatic'])

    def test_single_operation_cancel_and_no_secret_in_status(self):
        self.engine.work_lock.acquire()
        try:
            with self.assertRaises(cb.BackupError):
                self.engine.start('sync')
        finally:
            self.engine.work_lock.release()
        self.engine.stop.set()
        with self.assertRaises(cb.BackupError):
            list(self.engine.walk(self.source, self.engine.config, [], []))
        self.assertNotIn('test-access', json.dumps(self.engine.public()))

    def test_real_roundtrip_action_uses_download_hash_verification(self):
        self.engine.test_roundtrip()
        self.assertTrue(self.engine.state['lastTest']['ok'])
        self.assertEqual(self.fake.verifications, 1)
        self.assertTrue(any(x['name'] == '_connection-tests' for x in self.fake.objects.values()))


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.client-test-')
        self.base = Path(self.temp.name)
        self.client = cb.HamrahiClient(self.base)

    def tearDown(self):
        self.temp.cleanup()

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_dpapi_encrypts_session_and_recovers_it(self):
        tokens = {'access': 'synthetic-test-access', 'refresh': 'synthetic-test-refresh'}
        self.client.save_tokens(tokens)
        data = (self.base / 'session.bin').read_bytes()
        self.assertNotIn(b'synthetic-test', data)
        second = cb.HamrahiClient(self.base)
        self.assertEqual(second.tokens, tokens)
        second.forget()
        self.assertFalse((self.base / 'session.bin').exists())

    def test_multipart_streams_bounded_parts_and_no_bearer_to_storage(self):
        path = self.base / 'test.txt'
        path.write_bytes(b'abcdefghij')
        calls, complete = [], []
        def api(method, endpoint, payload=None, **kwargs):
            if endpoint.endswith('start-upload/'):
                return {'signed_urls': ['https://storage.example/a', 'https://storage.example/b'], 'chunk_size': 6, 'key': 'k', 'upload_id': 'u'}
            complete.append(payload)
            return {'id': 1, 'size': 10}
        def opened(request, timeout):
            self.assertNotIn('Authorization', request.headers)
            calls.append(b''.join(request.data))
            return Response(headers={'ETag': 'receipt-' + str(len(calls))})
        self.client._json = api
        self.client.opener.open = opened
        with patch.object(cb, 'check_transfer_url'):
            self.client.upload(path, 'test.txt', 7)
        self.assertEqual(calls, [b'abcdef', b'ghij'])
        self.assertEqual([p['size'] for p in complete[0]['parts']], [6, 4])
        self.assertFalse(complete[0]['force_overwrite'])

    def test_missing_receipt_does_not_complete_upload(self):
        path = self.base / 'x'
        path.write_bytes(b'x')
        def api(method, endpoint, payload=None, **kwargs):
            if endpoint.endswith('start-upload/'):
                return {'signed_urls': ['https://storage.example/a'], 'chunk_size': 1, 'key': 'k', 'upload_id': 'u'}
            self.fail('complete called without ETag')
        self.client._json = api
        self.client.opener.open = lambda *a, **k: Response()
        with patch.object(cb, 'check_transfer_url'), self.assertRaises(cb.BackupError):
            self.client.upload(path, 'x', None)

    def test_download_corruption_and_oversize_rejected(self):
        self.client._json = lambda *a, **k: {'download_url': 'https://storage.example/data'}
        for body in (b'bad', b'too long'):
            with self.subTest(body=body):
                self.client.opener.open = lambda *a, **k: Response(body)
                with patch.object(cb, 'check_transfer_url'), self.assertRaises(cb.BackupError):
                    self.client.verify(1, hashlib.sha256(b'one').hexdigest(), 3)

    def test_api_error_is_redacted_and_captcha_is_exposed(self):
        def opened(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 400, 'bad', {}, io.BytesIO(b'{"detail":"secret-token","captcha_required":123}'))
        self.client.opener.open = opened
        with self.assertRaises(cb.BackupError) as error:
            self.client._json('POST', 'v6/profile/auth/login/', {}, auth=False)
        self.assertTrue(error.exception.captcha)
        self.assertNotIn('secret-token', str(error.exception))

    def test_expired_access_refreshes_once(self):
        self.client.tokens = {'access': 'expired', 'refresh': 'synthetic'}
        paths = []
        def opened(request, timeout):
            paths.append(request.full_url)
            if 'token-refresh' in request.full_url:
                return Response(b'{"access":"renewed"}')
            if len(paths) == 1:
                raise urllib.error.HTTPError(request.full_url, 401, '', {}, io.BytesIO(b'{}'))
            self.assertEqual(request.headers['Authorization'], 'Bearer renewed')
            return Response(b'{"ok":true}')
        self.client.opener.open = opened
        with patch.object(cb, 'protect', lambda x, **k: x):
            self.assertTrue(self.client._json('GET', 'v6/profile/auth/get-profile/')['ok'])
        self.assertEqual(len(paths), 3)

    def test_private_transfer_destination_rejected(self):
        for url in ['http://example.com/file', 'https://user:pass@example.com/x', 'https://127.0.0.1/x']:
            with self.subTest(url=url), self.assertRaises(cb.BackupError):
                cb.check_transfer_url(url)
