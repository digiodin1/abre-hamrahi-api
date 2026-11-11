"""Open the existing panel, or start exactly one instance before opening it."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from .cli import default_data_dir, instance_lock


def ready(port):
    # A desktop launch must not inherit a system HTTP proxy for loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f'http://127.0.0.1:{port}/api/status', timeout=.5) as response:
            payload = json.loads(response.read(4096))
        return payload == {'app': 'abre-hamrahi-api', 'status': 'ready'}
    except (OSError, ValueError, urllib.error.URLError):
        return False


def open_panel(directory, port, open_browser=webbrowser.open, timeout=12):
    directory = Path(directory).expanduser().resolve()
    url = f'http://127.0.0.1:{port}/'
    if ready(port):
        open_browser(url)
        return 0
    executable = Path(sys.executable)
    if os.name == 'nt' and executable.with_name('pythonw.exe').is_file():
        executable = executable.with_name('pythonw.exe')
    command = [str(executable), str(Path(__file__).with_name('startup_runner.py')),
               '--data-dir', str(directory), '--port', str(port), '--no-browser']
    child = None
    deadline = time.monotonic() + timeout
    try:
        # Separate launch lock avoids duplicate children from a double click.
        with instance_lock(directory / 'desktop-launch'):
            if not ready(port):
                options = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL,
                           'stdin': subprocess.DEVNULL}
                if os.name == 'nt':
                    options['creationflags'] = subprocess.CREATE_NO_WINDOW
                child = subprocess.Popen(command, **options)
            while time.monotonic() < deadline:
                if ready(port):
                    open_browser(url)
                    return 0
                if child is not None and child.poll() is not None:
                    break
                time.sleep(.1)
    except RuntimeError:
        # Another desktop click owns launch, so wait for its readiness.
        while time.monotonic() < deadline:
            if ready(port):
                open_browser(url)
                return 0
            time.sleep(.1)
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='Start or open the local backup panel.')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', type=Path, default=default_data_dir())
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    try:
        result = open_panel(args.data_dir, args.port)
    except OSError:
        result = 1
    if result and os.name == 'nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,
            'پنل آماده نشد. پورت یا پوشهٔ تنظیمات ممکن است در اختیار اجرای دیگری باشد. دوباره تلاش کنید.',
            'Abre Hamrahi API', 0x10)
    return result
