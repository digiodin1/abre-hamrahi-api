"""Real CLI lifecycle checks use private temporary state and no cloud account."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class ShutdownTests(unittest.TestCase):
    def test_cli_exit_releases_instance_lock_and_keeps_saved_settings(self):
        root = Path(__file__).resolve().parents[1]
        env = os.environ | {'PYTHONPATH': str(root / 'src')}
        with tempfile.TemporaryDirectory() as directory:
            port = free_port()
            for attempt in range(2):
                # POSIX may retain TCP TIME_WAIT with address reuse disabled.
                if attempt and os.name != 'nt':
                    port = free_port()
                base = f'http://127.0.0.1:{port}'
                process = subprocess.Popen([sys.executable, '-m', 'hamrahi_backup',
                    '--data-dir', directory, '--port', str(port), '--no-browser'],
                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 10
                    model = None
                    while time.monotonic() < deadline and process.poll() is None:
                        try:
                            with urllib.request.urlopen(base + '/api/backup', timeout=1) as response:
                                model = json.load(response)
                            break
                        except (urllib.error.URLError, TimeoutError):
                            time.sleep(0.05)
                    self.assertIsNotNone(model, 'isolated CLI did not become ready')
                    self.assertFalse(model['authenticated'])
                    headers = {'Content-Type': 'application/json', 'Origin': base,
                               'X-Abr-Hamrahi-CSRF': model['csrfToken']}
                    if not attempt:
                        config = model['config'] | {'remoteRoot': 'SYNTHETIC-EXIT-TEST'}
                        request = urllib.request.Request(base + '/api/backup/config',
                            data=json.dumps(config).encode(), headers=headers)
                        with urllib.request.urlopen(request, timeout=3) as response:
                            self.assertEqual(response.status, 200)
                    else:
                        self.assertEqual(model['config']['remoteRoot'], 'SYNTHETIC-EXIT-TEST')
                    request = urllib.request.Request(base + '/api/backup/shutdown',
                        data=b'{"confirm":true}', headers=headers)
                    with urllib.request.urlopen(request, timeout=3) as response:
                        self.assertTrue(json.load(response)['shuttingDown'])
                    self.assertEqual(process.wait(timeout=10), 0)
                finally:
                    if process.poll() is None:
                        # Only this test's own unauthenticated child can reach here.
                        process.terminate()
                        process.wait(timeout=5)
                    process.stderr.close()
