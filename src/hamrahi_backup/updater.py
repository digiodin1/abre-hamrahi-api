"""Bounded HTTPS updater for a fixed GitHub repository; no cloud credentials."""
from datetime import datetime
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

REPO = 'digiodin1/abre-hamrahi-api'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
WINDOW = 30 * 86400
MAX_INSTALLER = 500 * 1024 * 1024
HOSTS = {'github.com', 'api.github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}

def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?\d+\.\d+\.\d+', value):
        raise ValueError('Invalid stable version')
    return tuple(map(int, value.lstrip('v').split('.')))

def safe_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Untrusted update URL')
    return url

class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def open_url(url, timeout=10):
    request = urllib.request.Request(safe_url(url), headers={'User-Agent': 'AbreHamrahiAPI-updater', 'Accept': 'application/vnd.github+json'})
    return urllib.request.build_opener(Redirects()).open(request, timeout=timeout)

def select_release(release, current, clock=None):
    tag = release.get('tag_name', '')
    if release.get('draft') or release.get('prerelease') or version(tag) <= version(current):
        return {'available': False, 'current': current}
    published = datetime.fromisoformat(release['published_at'].replace('Z', '+00:00'))
    if published.tzinfo is None:
        raise ValueError('Missing release timezone')
    name = f'AbreHamrahiAPI-{tag.lstrip("v")}-windows-x64-setup.exe'
    expected_url = f'https://github.com/{REPO}/releases/download/{tag}/{name}'
    asset = next((a for a in release.get('assets', []) if a.get('name') == name and a.get('browser_download_url') == expected_url and a.get('state') == 'uploaded'), None)
    if not asset:
        return {'available': False, 'current': current, 'error': 'فایل نصب ویندوز هنوز منتشر نشده است.'}
    if not re.fullmatch(r'sha256:[a-fA-F0-9]{64}', asset.get('digest') or '') or not 10000 <= asset.get('size', 0) <= MAX_INSTALLER:
        raise ValueError('Missing digest or invalid size')
    now = time.time() if clock is None else clock
    if published.timestamp() > now + 86400:
        raise ValueError('Future release date')
    return {'available': True, 'mandatory': now >= published.timestamp() + WINDOW, 'current': current, 'version': tag.lstrip('v'),
            'publishedAt': release['published_at'], 'deadline': published.timestamp() + WINDOW,
            'ageDays': max(0, int((now - published.timestamp()) // 86400)),
            'url': expected_url, 'size': asset['size'], 'sha256': asset['digest'][7:].lower()}

def check(current):
    with open_url(API) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError('Release response too large')
    return select_release(json.loads(data), current)

class Updater:
    def __init__(self, directory, current):
        self.directory, self.current = Path(directory), current
        self.lock = threading.RLock()
        self.info = {'available': False, 'current': current}
        self.checked = 0
        self.checking = False
        self.installing = False
        try:
            cached = json.loads((self.directory / 'update-release.json').read_text('utf-8'))
            if version(cached.get('version')) > version(current) and cached.get('available') and isinstance(cached.get('deadline'), (int, float)):
                self.info = cached
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def snapshot(self):
        with self.lock:
            result = dict(self.info)
            if result.get('available'):
                result['mandatory'] = time.time() >= result['deadline']
            return result | {'checking': self.checking, 'installing': self.installing}

    def refresh(self):
        try:
            value = check(self.current)
            with self.lock:
                self.info = value
                self.directory.mkdir(parents=True, exist_ok=True)
                target = self.directory / 'update-release.json'
                temp = target.with_suffix('.tmp')
                temp.write_text(json.dumps(value), encoding='utf-8')
                temp.replace(target)
        except Exception:
            with self.lock:
                self.info['error'] = 'بررسی به‌روزرسانی ناموفق بود؛ دوباره تلاش کنید.'
        finally:
            self.checked = time.monotonic()
        return self.snapshot()

    def tick(self, force=False):
        with self.lock:
            if self.checking or (not force and self.checked and time.monotonic() - self.checked < 21600):
                return
            self.checking = True
        def run():
            try:
                self.refresh()
            finally:
                self.checking = False
        threading.Thread(target=run, daemon=True, name='release-check').start()

    def download(self, info):
        version(info['version'])
        name = f'AbreHamrahiAPI-{info["version"]}-windows-x64-setup.exe'
        expected = f'https://github.com/{REPO}/releases/download/v{info["version"]}/{name}'
        if info.get('url') != expected or not re.fullmatch('[a-f0-9]{64}', info.get('sha256', '')):
            raise ValueError('Invalid installer metadata')
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / name
        temporary = target.with_suffix('.part')
        digest, total = hashlib.sha256(), 0
        try:
            with open_url(expected, 30) as response, temporary.open('wb') as output:
                while block := response.read(128 * 1024):
                    total += len(block)
                    if total > min(MAX_INSTALLER, info['size']):
                        raise ValueError('Installer too large')
                    output.write(block)
                    digest.update(block)
            if total != info['size'] or digest.hexdigest() != info['sha256']:
                raise ValueError('Installer checksum mismatch')
            temporary.replace(target)
            return target
        finally:
            temporary.unlink(missing_ok=True)

def launch_installer(installer, restart_args):
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        raise RuntimeError('از نسخهٔ نصب‌شدهٔ ویندوز به‌روزرسانی کنید.')
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    exe = Path(sys.executable)
    parameters = subprocess.list2cmdline(['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/DIR=' + str(exe.parent)])
    restart = subprocess.list2cmdline(restart_args)
    script = (f'Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; '
              f'$updateProcess = Start-Process -FilePath {quote(installer)} -ArgumentList {quote(parameters)} -Wait -PassThru -WindowStyle Hidden; '
              f'if ($updateProcess.ExitCode -eq 0) {{ Start-Process -FilePath {quote(exe)} -ArgumentList {quote(restart)} -WindowStyle Hidden }}')
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    subprocess.Popen([str(Path(os.environ.get('WINDIR', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
                      '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], creationflags=subprocess.CREATE_NO_WINDOW)
