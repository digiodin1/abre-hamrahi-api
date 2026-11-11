"""Real Windows bundle/install lifecycle check with synthetic, isolated state.

This is opt-in: it refuses to change an existing installed product. It never
loads a personal session or contacts the cloud. Reports stay in ignored .qa.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--skip-picker', action='store_true', help='For non-interactive CI desktops only')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Windows only')
    import winreg
    product_key = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\AbreHamrahiAPI_is1'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, product_key):
            parser.error('Product already installed; refusing to change it')
    except FileNotFoundError:
        pass
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / 'src'))
    qa = repo / '.qa' / 'windows-package-check'
    qa.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='run-', dir=qa))
    env = {key: value for key, value in os.environ.items() if key not in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV')}
    env['PATH'] = str(Path(os.environ['SYSTEMROOT']) / 'System32')
    env['LOCALAPPDATA'] = str(root / 'isolated-profile')
    bundle = args.bundle.resolve()
    installer = args.installer.resolve()
    checks = []
    flags = subprocess.CREATE_NO_WINDOW
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    profile = root / 'private state حساب'
    installed = root / 'نصب آزمایشی با فاصله'
    source = root / 'synthetic source'
    source.mkdir()
    (source / 'نمونه اصلی.txt').write_text('synthetic offline preview\n', encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'

    def run(command, timeout=45):
        return subprocess.run([str(item) for item in command], cwd=root, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, timeout=timeout)

    def read():
        with opener.open(base + '/api/backup', timeout=2) as response:
            return json.load(response)

    def action(name, data=None):
        state = read()
        request = urllib.request.Request(base + '/api/backup/' + name,
            data=json.dumps(data or {}).encode(), headers={'Content-Type': 'application/json',
                'Origin': base, 'X-Abr-Hamrahi-CSRF': state['csrfToken']})
        with opener.open(request, timeout=10) as response:
            return json.load(response)

    def wait_ready():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                return read()
            except (OSError, urllib.error.URLError):
                time.sleep(.1)
        raise AssertionError('Bundle did not become ready')

    def wait_idle():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = read()
            if not state['state']['busy']:
                return state
            time.sleep(.1)
        raise AssertionError('Offline worker did not finish')

    def stop():
        try:
            action('shutdown', {'confirm': True})
        except (OSError, urllib.error.URLError):
            return
        sys.path.insert(0, str(repo / 'src'))
        from hamrahi_backup.cli import instance_lock
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with instance_lock(profile):
                    return
            except RuntimeError:
                time.sleep(.1)
        raise AssertionError('Bundle did not release its profile lock')

    def install():
        command = [installer, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS',
            '/TASKS=', '/DIR=' + str(installed), '/GROUP=Abre Hamrahi API Package QA',
            '/LOG=' + str(root / 'install.log')]
        return run(command)

    def diagnostics(exe, name):
        output = root / (name + '.json')
        assert run([exe, '--diagnostics', output]).returncode == 0, 'Bundled runtime diagnostic failed'
        report = json.loads(output.read_text('utf-8'))
        assert report['frozen'] and report['dpapiRoundTrip'] and report['webAssets']
        assert report['nativeObserver'] == 'WindowsApiObserver'
        return report

    def check_picker(exe):
        output = root / 'picker.json'
        process = subprocess.Popen([str(exe), '--folder-picker', str(output)], cwd=root, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        user = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        user.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        found = []
        def visit(hwnd, _):
            owner = ctypes.c_ulong()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            title = ctypes.create_unicode_buffer(256)
            user.GetWindowTextW(ctypes.c_void_p(hwnd), title, len(title))
            if owner.value == process.pid and 'Select a folder to sync' in title.value:
                found.append(hwnd)
                user.PostMessageW(hwnd, 0x0010, 0, 0)
            return True
        callback = callback_type(visit)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and process.poll() is None and not found:
                user.EnumWindows(callback, None)
                time.sleep(.1)
            assert found, 'Bundled folder dialog did not open'
            assert process.wait(timeout=10) == 0
            assert json.loads(output.read_text('utf-8')) == {'path': '', 'cancelled': True}
        finally:
            if process.poll() is None:
                process.terminate()  # Only the explicitly created disposable chooser process.
                process.wait(timeout=5)

    success = False
    try:
        moved = root / 'portable moved مسیر فارسی'
        shutil.copytree(bundle, moved)
        diagnostics(moved / 'AbreHamrahiAPI.exe', 'portable-diagnostics')
        checks.append('Relocated portable bundle: Tcl/Tk, assets, DPAPI and native watcher with no Python on PATH')
        if not args.skip_picker:
            check_picker(moved / 'AbreHamrahiAPI.exe')
            checks.append('Real native folder chooser opened and cancelled in bundled child')
        assert install().returncode == 0, 'Installer failed'
        exe = installed / 'AbreHamrahiAPI.exe'
        assert exe.is_file() and (installed / 'unins000.exe').is_file()
        diagnostics(exe, 'installed-diagnostics')
        checks.append('Actual per-user installer ran in a Unicode path')
        links = root / 'shortcut destination'
        links.mkdir()
        assert run([exe, '--create-shortcuts', '--desktop', links, '--data-dir', profile, '--port', str(port)]).returncode == 0
        link_bytes = (links / 'Abre Hamrahi API.lnk').read_bytes()
        assert str(profile).encode('utf-16-le') in link_bytes
        assert ('--port ' + str(port)).encode('utf-16-le') in link_bytes
        checks.append('Bundled shortcut creation preserves private profile and port in Unicode paths')
        command = [exe, '--headless-open', '--data-dir', profile, '--port', str(port)]
        assert run(command).returncode == 0
        initial = wait_ready()
        assert not initial['authenticated'] and initial['config']['folders'] == []
        assert not initial['config']['automatic'] and not initial['startup']['registered']
        token = initial['csrfToken']
        assert run(command).returncode == 0
        assert read()['csrfToken'] == token
        checks.append('Cold launch and repeat launch reuse one panel; no preloaded account or sources')
        config = initial['config'] | {'folders': [{'path': str(source)}], 'settleSeconds': 0}
        action('config', config)
        action('preview')
        preview = wait_idle()
        assert preview['state']['lastResult']['ok'] and preview['state']['lastResult']['files'] == 1
        assert preview['state']['progress']['percent'] == 100
        checks.append('Real packaged HTTP server, packaged UI assets and synthetic offline preview')
        action('config', preview['config'] | {'startupEnabled': True})
        registered = read()
        assert registered['startup']['registered']
        from hamrahi_backup.startup import StartupRegistration
        registration = StartupRegistration(profile, port)
        registered_command = registration.read()
        shell = ctypes.windll.shell32
        shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
        shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
        count = ctypes.c_int()
        pointer = shell.CommandLineToArgvW(registered_command, ctypes.byref(count))
        try:
            parts = list(pointer[:count.value])
        finally:
            ctypes.windll.kernel32.LocalFree(ctypes.cast(pointer, ctypes.c_void_p))
        assert Path(parts[0]).resolve() == exe.resolve() and parts[1] == '--serve'
        assert Path(parts[parts.index('--data-dir') + 1]).resolve() == profile.resolve()
        assert parts[parts.index('--port') + 1] == str(port)
        assert '.py' not in registered_command
        action('config', registered['config'] | {'startupEnabled': False})
        expected = read()['config']
        checks.append('Startup registers installed executable and custom state/port, then removes entry')
        assert install().returncode != 0, 'Installer must refuse a running backup'
        assert read()['csrfToken'] == token
        checks.append('Running application blocks installer without killing the backup')
        stop()
        assert install().returncode == 0, 'Repair/upgrade failed'
        assert run(command).returncode == 0
        assert wait_ready()['config'] == expected
        checks.append('Repair/upgrade preserves exact private configuration')
        stop()
        assert run([installed / 'unins000.exe', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART']).returncode == 0
        deadline = time.monotonic() + 15
        while exe.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        assert not exe.exists()
        assert (profile / 'config.json').is_file()
        assert (source / 'نمونه اصلی.txt').read_text('utf-8') == 'synthetic offline preview\n'
        assert registration.read() is None
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, product_key):
                raise AssertionError('Product uninstall entry remained after uninstall')
        except FileNotFoundError:
            pass
        checks.append('Uninstall removes program and leaves private state and source data intact')
        success = True
    finally:
        stop()
        sys.path.insert(0, str(repo / 'src'))
        from hamrahi_backup.startup import StartupRegistration
        StartupRegistration(profile, port).configure(False)
        if not success and (installed / 'unins000.exe').is_file():
            run([installed / 'unins000.exe', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'])
        report = {'passed': success, 'checks': checks,
                  'installerSHA256': hashlib.sha256(installer.read_bytes()).hexdigest()}
        (root / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report))


if __name__ == '__main__':
    main()
