import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from hamrahi_backup import engine
from test_engine import FakeCloud, Response


class TransferPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.cloud = FakeCloud()
        self.manager = engine.BackupManager(self.root / 'state', self.cloud)
        self.manager.save_config(self.manager.config | {'settleSeconds': 0, 'verifyUploads': False, 'uploadWorkers': 4,
            'folders': [{'path': str(self.source)}]})

    def tearDown(self):
        self.temporary.cleanup()

    def test_four_files_transfer_concurrently_without_snapshot_cross_contamination(self):
        for number in range(8):
            (self.source / f'{number}.txt').write_bytes(bytes([number]) * (number + 1))
        barrier, guard = threading.Barrier(4), threading.Lock()
        active, peak = 0, 0
        upload = self.cloud.upload
        def transfer(path, name, parent, **options):
            nonlocal active, peak
            if name.startswith('_manifest'):
                return upload(path, name, parent, **options)
            with guard:
                active += 1
                peak = max(peak, active)
            try:
                barrier.wait(timeout=5)
                with guard:
                    return upload(path, name, parent, **options)
            finally:
                with guard:
                    active -= 1
        with patch.object(self.cloud, 'upload', side_effect=transfer):
            self.manager.sync()
        self.assertEqual(peak, 4)
        self.assertTrue(self.manager.state['lastResult']['ok'])
        objects = {item['name']: item for item in self.cloud.objects.values() if item.get('key')}
        for number in range(8):
            self.assertEqual(objects[f'{number}.txt']['data'], bytes([number]) * (number + 1))
        self.assertEqual(self.cloud.verifications, 0)
        self.assertEqual(self.manager.public()['syncMatch']['matchedBytes'], 36)
        self.assertEqual(self.manager.public()['syncMatch']['assurance'], 'volume-similarity')

    def test_completed_small_file_does_not_hide_large_remaining_file(self):
        (self.source / 'small.txt').write_bytes(b'x')
        (self.source / 'large.txt').write_bytes(b'x' * 999)
        upload = self.cloud.upload
        def transfer(path, name, parent, **options):
            if name == 'large.txt':
                raise engine.BackupError('synthetic rejected upload')
            return upload(path, name, parent, **options)
        with patch.object(self.cloud, 'upload', side_effect=transfer):
            self.manager.sync()
        model = self.manager.public()
        self.assertEqual(model['syncMatch']['totalBytes'], 1000)
        self.assertEqual(model['syncMatch']['matchedBytes'], 1)
        self.assertEqual(model['syncMatch']['remainingBytes'], 999)
        self.assertAlmostEqual(model['syncMatch']['percent'], .1)
        self.assertAlmostEqual(model['state']['progress']['percent'], .1)
        self.assertFalse(model['syncMatch']['complete'])

    def test_independent_api_calls_do_not_hold_the_session_lock_during_network_io(self):
        client = engine.HamrahiClient(self.root / 'client')
        client.tokens = {'access': 'synthetic'}
        barrier = threading.Barrier(4)
        def opened(request, timeout):
            barrier.wait(timeout=5)
            return Response(json.dumps({'ok': True}).encode())
        with patch.object(client.opener, 'open', side_effect=opened):
            with engine.ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(lambda number: client._json('GET', f'v2/flat/get-object/{number}/'), range(4)))
        self.assertEqual(results, [{'ok': True}] * 4)

    def test_upload_requires_fresh_size_identity_name_parent_and_key(self):
        source = self.source / 'example.txt'
        source.write_bytes(b'test')
        client = engine.HamrahiClient(self.root / 'client')
        valid = {'id': 7, 'size': 4, 'name': source.name, 'parent': 8, 'key': 'key'}
        for changed in ({'size': 3}, {'id': 9}, {'name': 'renamed.txt'}, {'parent': 9}, {'key': 'different'}):
            calls = []
            def api(method, endpoint, payload=None, **options):
                calls.append((method, endpoint))
                if endpoint.endswith('start-upload/'):
                    return {'signed_urls': ['https://storage.example/data'], 'chunk_size': 4,
                            'key': 'key', 'upload_id': 'synthetic'}
                return valid | changed if method == 'GET' else valid
            with self.subTest(changed=changed), patch.object(client, '_json', side_effect=api), patch.object(engine.time, 'sleep'):
                with patch.object(client.opener, 'open', return_value=Response(headers={'ETag': 'receipt'})):
                    with patch.object(engine, 'check_transfer_url'), self.assertRaises(engine.BackupError):
                        client.upload(source, source.name, 8)
            self.assertEqual(calls[-1], ('GET', 'v2/flat/get-object/7/'))

    def test_finalized_storage_key_and_delayed_metadata_do_not_trigger_reupload(self):
        source = self.source / 'example.txt'
        source.write_bytes(b'test')
        client = engine.HamrahiClient(self.root / 'client')
        valid = {'id': 7, 'size': 4, 'name': source.name, 'parent': 8, 'key': 'final-storage-key'}
        reads, posts = [], []
        def api(method, endpoint, payload=None, **options):
            if endpoint.endswith('start-upload/'):
                posts.append(endpoint)
                return {'signed_urls': ['https://storage.example/data'], 'chunk_size': 4,
                        'key': 'temporary-upload-key', 'upload_id': 'synthetic'}
            if method == 'POST':
                posts.append(endpoint)
                return valid
            reads.append(endpoint)
            return valid | {'size': 0} if len(reads) == 1 else valid
        with patch.object(client, '_json', side_effect=api), \
                patch.object(client.opener, 'open', return_value=Response(headers={'ETag': 'receipt'})), \
                patch.object(engine, 'check_transfer_url'), patch.object(engine.time, 'sleep'):
            result = client.upload(source, source.name, 8)
        self.assertEqual(result, valid)
        self.assertEqual(len(posts), 2)
        self.assertEqual(len(reads), 2)

    def test_hash_check_setting_does_not_change_volume_ratio(self):
        (self.source / 'hello.txt').write_bytes(b'hello')
        self.manager.sync()
        self.assertEqual(self.manager.public()['syncMatch']['percent'], 100)
        self.manager.save_config(self.manager.config | {'verifyUploads': True})
        self.assertEqual(self.manager.public()['syncMatch']['percent'], 100)
        self.assertTrue(self.manager.public()['syncMatch']['stale'])
