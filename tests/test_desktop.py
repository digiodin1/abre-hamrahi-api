"""Launcher checks use synthetic local processes and never open the browser."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import urllib.request

from hamrahi_backup import desktop
from hamrahi_backup.shortcuts import create_shortcuts
from test_shutdown import free_port


class DesktopTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows Shell shortcuts')
    def test_windows_shortcut_roundtrip_with_spaces_and_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'تنظیمات خصوصی'
            link = create_shortcuts(state, 8799, desktop=root)
            self.assertTrue(link.is_file())
            env = os.environ | {'ABRE_TEST_LINK': str(link)}
            command = '$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut($env:ABRE_TEST_LINK); @{' + \
                      'target=$s.TargetPath; arguments=$s.Arguments} | ConvertTo-Json -Compress'
            raw = desktop.subprocess.check_output(['powershell', '-NoProfile', '-Command', command],
                                                  env=env)
            result = json.loads(raw.decode('utf8'))
            self.assertTrue(Path(result['target']).is_file())
            self.assertIn('desktop_runner.py', result['arguments'])
            # Windows PowerShell output encoding varies; compare parameters that
            # are invariant, and use the link's UTF-16 content for Unicode.
            self.assertIn('--port 8799', result['arguments'])
            self.assertIn(str(state.resolve()).encode('utf-16-le'), link.read_bytes())
            self.assertIn('https://abrehamrahi.ir/drive/files',
                          (root/'Abre Hamrahi Cloud.url').read_text())

    def test_existing_panel_opens_without_spawning_process(self):
        opened = Mock()
        with patch.object(desktop, 'ready', return_value=True), patch.object(desktop.subprocess, 'Popen') as spawn:
            self.assertEqual(desktop.open_panel(Path('unused'), 8765, opened), 0)
        spawn.assert_not_called()
        opened.assert_called_once_with('http://127.0.0.1:8765/')

    def test_duplicate_click_waits_for_other_launcher(self):
        @contextmanager
        def busy(_):
            raise RuntimeError('launch locked')
            yield
        opened = Mock()
        with patch.object(desktop, 'ready', side_effect=[False, True]), \
             patch.object(desktop, 'instance_lock', busy), patch.object(desktop.subprocess, 'Popen') as spawn:
            self.assertEqual(desktop.open_panel(Path('unused'), 8765, opened), 0)
        spawn.assert_not_called()
        opened.assert_called_once()

    def test_failed_child_does_not_open_unready_page(self):
        child, opened = Mock(), Mock()
        child.poll.return_value = 1
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(desktop, 'ready', return_value=False), \
             patch.object(desktop.subprocess, 'Popen', return_value=child):
            self.assertEqual(desktop.open_panel(directory, 8765, opened), 1)
        opened.assert_not_called()

    def test_stopped_panel_starts_and_repeat_launch_reuses_it(self):
        with tempfile.TemporaryDirectory() as directory:
            port, opened = free_port(), Mock()
            children, spawn_child = [], desktop.subprocess.Popen
            def retain(*args, **kwargs):
                child = spawn_child(*args, **kwargs)
                children.append(child)
                return child
            base = f'http://127.0.0.1:{port}'
            try:
                with patch.object(desktop.subprocess, 'Popen', side_effect=retain):
                    self.assertEqual(desktop.open_panel(directory, port, opened), 0)
                with patch.object(desktop.subprocess, 'Popen') as spawn:
                    self.assertEqual(desktop.open_panel(directory, port, opened), 0)
                spawn.assert_not_called()
                self.assertEqual(opened.call_count, 2)
                with urllib.request.urlopen(base + '/api/backup', timeout=3) as response:
                    state = json.load(response)
                self.assertFalse(state['authenticated'])
            finally:
                if desktop.ready(port):
                    with urllib.request.urlopen(base + '/api/backup', timeout=3) as response:
                        state = json.load(response)
                    req = urllib.request.Request(base + '/api/backup/shutdown', data=b'{"confirm":true}',
                        headers={'Origin': base, 'Content-Type': 'application/json',
                                 'X-Abr-Hamrahi-CSRF': state['csrfToken']})
                    with urllib.request.urlopen(req, timeout=3):
                        pass
                # Wait for the CLI's state lock, not just its HTTP acknowledgement.
                deadline = time.monotonic() + 5
                while True:
                    try:
                        with desktop.instance_lock(Path(directory)):
                            break
                    except RuntimeError:
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(.05)
                for child in children:
                    self.assertEqual(child.wait(timeout=5), 0)
