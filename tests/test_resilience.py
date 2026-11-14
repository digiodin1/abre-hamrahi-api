import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from hamrahi_backup import engine as cb
from hamrahi_backup.cli import instance_lock, main
from test_engine import FakeCloud, Response


class ResilienceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.fake = FakeCloud()
        self.engine = cb.BackupManager(self.base, self.fake)
        config = self.engine.public()['config']
        config.update(syncBehavior='versioned', settleSeconds=0, folders=[{'path': str(self.source), 'name': 'Documents'}])
        self.engine.save_config(config)

    def tearDown(self):
        self.temp.cleanup()

    def files(self):
        return [x for x in self.fake.objects.values() if x.get('key') and not x['name'].startswith('_manifest')]

    def test_future_config_and_non_integer_values_leave_original_intact(self):
        before = self.engine.config_path.read_bytes()
        for config in [self.engine.config | {'version': 2},
                       self.engine.config | {'intervalMinutes': True},
                       self.engine.config | {'maxFileMiB': 1.5}]:
            with self.assertRaises(cb.BackupError):
                self.engine.save_config(config)
            self.assertEqual(self.engine.config_path.read_bytes(), before)

    def test_config_cannot_race_a_running_operation(self):
        self.engine.work_lock.acquire()
        try:
            with self.assertRaises(cb.BackupError):
                self.engine.save_config(self.engine.config)
        finally:
            self.engine.work_lock.release()

    def test_atomic_write_failure_preserves_valid_config(self):
        before = self.engine.config_path.read_bytes()
        with patch.object(cb.os, 'fsync', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                self.engine.save_config(self.engine.config | {'remoteRoot': 'Other'})
        self.assertEqual(self.engine.config_path.read_bytes(), before)
        self.assertEqual(list(self.engine.state_dir.glob('config.json-*.tmp')), [])
        self.assertTrue(self.engine.work_lock.acquire(False))
        self.engine.work_lock.release()

    def test_absolute_source_path_never_appears_in_cloud_manifest(self):
        (self.source / 'hello.txt').write_text('hello')
        self.engine.sync()
        manifests = [x['data'] for x in self.fake.objects.values() if x['name'].startswith('_manifest')]
        self.assertNotIn(str(self.source), manifests[0].decode())
        self.assertEqual(json.loads(manifests[0])['files'][0]['path'], 'hello.txt')

    def test_remote_corruption_detected_even_when_index_matches(self):
        (self.source / 'hello.txt').write_text('one')
        self.engine.sync()
        self.files()[0]['data'] = b'two'
        self.engine.sync()
        self.assertFalse(self.engine.state['lastResult']['ok'])

    def test_duplicate_cloud_names_are_never_adopted(self):
        (self.source / 'hello.txt').write_text('one')
        self.engine.sync()
        obj = self.files()[0]
        self.fake.objects[999] = obj | {'id': 999}
        with self.assertRaises(cb.BackupError):
            self.engine.sync()

    def test_quota_exhaustion_never_indexes_a_file(self):
        (self.source / 'hello.txt').write_text('one')
        self.fake.connection = lambda: {'used': 10, 'total': 10}
        self.engine.sync()
        self.assertFalse(self.engine.state['lastResult']['ok'])
        self.assertEqual(self.files(), [])

    def test_single_instance_lock_releases_after_failure(self):
        with instance_lock(self.base):
            with self.assertRaises(RuntimeError):
                with instance_lock(self.base):
                    self.fail('A second process obtained the same directory')
        with instance_lock(self.base):
            pass


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.client = cb.HamrahiClient(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_unexpected_api_shapes_fail_closed(self):
        for raw in [b'[]', b'null', b'"string"']:
            self.client.opener.open = lambda *a, **k: Response(raw)
            with self.assertRaises(cb.BackupError):
                self.client._json('POST', 'v6/profile/auth/login/', {}, auth=False)

    def test_non_object_error_body_is_redacted(self):
        def opened(*args, **kwargs):
            raise urllib.error.HTTPError('https://example.invalid', 400, '', {}, io.BytesIO(b'[]'))
        self.client.opener.open = opened
        with self.assertRaises(cb.BackupError):
            self.client._json('POST', 'v6/profile/auth/login/', {}, auth=False)

    def test_reads_retry_but_ambiguous_writes_do_not(self):
        calls = []
        def opened(*args, **kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise urllib.error.URLError('synthetic')
            return Response(b'{}')
        self.client.opener.open = opened
        with patch.object(cb.time, 'sleep'):
            self.client._json('GET', 'v1/captcha/get-captcha/', auth=False)
        self.assertEqual(len(calls), 3)
        calls.clear()
        with self.assertRaises(cb.BackupError):
            self.client._json('POST', 'v6/profile/auth/login/', {}, auth=False)
        self.assertEqual(len(calls), 1)

    def test_upload_cancellation_never_calls_completion(self):
        file = self.path / 'x'
        file.write_bytes(b'hello')
        calls = []
        def api(*args, **kwargs):
            calls.append(args[1])
            return {'signed_urls': ['https://storage.example/x'], 'chunk_size': 5, 'key': 'k', 'upload_id': 'u'}
        self.client._json = api
        def stop():
            if calls:
                raise cb.BackupError('cancelled')
        with self.assertRaises(cb.BackupError):
            self.client.upload(file, 'x', 1, cancelled=stop)
        self.assertEqual(len(calls), 1)

    def test_download_checks_cancellation_between_blocks(self):
        self.client._json = lambda *a, **k: {'download_url': 'https://storage.example/x'}
        self.client.opener.open = lambda *a, **k: Response(b'hello')
        calls = []
        def stop():
            calls.append(1)
            if len(calls) == 3:
                raise cb.BackupError('cancelled')
        with patch.object(cb, 'check_transfer_url'), self.assertRaises(cb.BackupError):
            self.client.verify(1, hashlib.sha256(b'hello').hexdigest(), 5, cancelled=stop)

    def test_unsupported_ports_and_empty_tokens_rejected(self):
        for url in ['https://example.com:22/x', 'https://example.com:99999/x', 12]:
            with self.assertRaises(cb.BackupError):
                cb.check_transfer_url(url)
        for tokens in [{'access': '', 'refresh': 'x'}, {'access': 'x'}]:
            with self.assertRaises(cb.BackupError):
                self.client.save_tokens(tokens)

    def test_transfer_dns_changes_are_rejected(self):
        public = [(None, None, None, None, ('93.184.216.34', 443))]
        private = [(None, None, None, None, ('127.0.0.1', 443))]
        with patch.object(cb.socket, 'getaddrinfo', side_effect=[public, private]):
            with self.assertRaises(cb.BackupError):
                cb.check_transfer_url('https://storage.example/x')

    def test_cli_version_does_not_access_account(self):
        with self.assertRaises(SystemExit) as result:
            main(['--version'])
        self.assertEqual(result.exception.code, 0)
