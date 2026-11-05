"""HTTP trust-boundary tests use an isolated loopback server and synthetic files."""
import json
import http.client
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from hamrahi_backup.engine import BackupManager
from hamrahi_backup.server import PanelServer
from test_engine import FakeCloud


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = BackupManager(Path(self.temp.name), FakeCloud())
        self.server = PanelServer(self.engine, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        req = urllib.request.Request(self.base + path, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.headers, error.read()

    def post(self, data, headers=None, path='/api/backup/config'):
        if headers is None:
            headers = {
            'Content-Type': 'application/json', 'Origin': self.base,
            'X-Abr-Hamrahi-CSRF': self.server.csrf,
            }
        return self.request(path, data, headers)

    def test_packaged_assets_and_security_headers(self):
        for path, mime in [('/', 'text/html'), ('/backup.js', 'text/javascript'),
                           ('/backup.css', 'text/css'), ('/api/backup', 'application/json')]:
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertTrue(headers['Content-Type'].startswith(mime))
                self.assertEqual(int(headers['Content-Length']), len(body))
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(headers['X-Frame-Options'], 'DENY')
                self.assertEqual(headers['Cross-Origin-Resource-Policy'], 'same-origin')
                self.assertEqual(headers['Permissions-Policy'], 'camera=(), microphone=(), geolocation=()')
                self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
                self.assertNotIn('Access-Control-Allow-Origin', headers)
        state = json.loads(self.request('/api/backup')[2])
        self.assertEqual(state['csrfToken'], self.server.csrf)
        self.assertNotIn('test-access', json.dumps(state))

    def test_cross_site_dns_rebinding_and_missing_csrf_rejected(self):
        good = {'Content-Type': 'application/json', 'Origin': self.base,
            'X-Abr-Hamrahi-CSRF': self.server.csrf}
        for headers in [{}, good | {'X-Abr-Hamrahi-CSRF': 'invalid-token'},
                        good | {'Origin': 'https://evil.example'},
                        good | {'Host': 'evil.example'},
                        good | {'Sec-Fetch-Site': 'cross-site'}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.post(b'{}', headers)[0], 403)
        self.assertEqual(self.request('/api/backup', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/api/backup', headers={'Origin': 'https://evil.example'})[0], 403)

    def test_no_path_traversal_or_runtime_file_download(self):
        for path in ['/../engine.py', '/%2e%2e/engine.py', '/session.bin',
                     '/api/inventory', '/.backup/config.json', '/backup.js?file=secret']:
            self.assertEqual(self.request(path)[0], 404)

    def test_body_limits_types_and_errors_do_not_change_settings(self):
        before = self.engine.public()['config']
        for body in [b'{', b'[]', b'null', b'\xff']:
            self.assertEqual(self.post(body)[0], 400)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request('POST', '/api/backup/config', body=b'', headers={
            'Content-Type': 'application/json', 'Origin': self.base,
            'X-Abr-Hamrahi-CSRF': self.server.csrf, 'Content-Length': '65537'})
        self.assertEqual(connection.getresponse().status, 413)
        connection.close()
        headers = {'Content-Type': 'text/plain', 'Origin': self.base, 'X-Abr-Hamrahi-CSRF': self.server.csrf}
        self.assertEqual(self.post(b'{}', headers)[0], 415)
        self.assertEqual(self.engine.public()['config'], before)
        self.assertEqual(self.post(b'{}', path='/api/backup/not-real')[0], 404)

    def test_valid_config_and_offline_preview(self):
        source = Path(self.temp.name) / 'source'
        source.mkdir()
        (source / 'hello.txt').write_text('synthetic sample')
        config = self.engine.public()['config'] | {'settleSeconds': 0,
                                                  'folders': [{'path': str(source), 'name': 'Example'}]}
        self.assertEqual(self.post(json.dumps(config).encode())[0], 200)
        self.engine.client.connection = lambda: self.fail('Preview contacted cloud')
        self.assertEqual(self.post(b'{}', path='/api/backup/preview')[0], 200)
        self.assertTrue(self.engine.work_lock.acquire(timeout=5))
        self.engine.work_lock.release()
        self.assertEqual(self.engine.state['lastResult']['uploaded'], 1)

    def test_server_is_bound_to_ipv4_loopback(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
