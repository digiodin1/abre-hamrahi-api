"""One-way, versioned Abrehamrahi backup. No browser session is used.

The provider's private web API was inspected on 2026-11-04. It is not a
published integration contract; reject unexpected responses instead of guessing.
"""
from contextlib import closing, contextmanager
import ctypes
from ctypes import wintypes
import datetime as dt
import fnmatch
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .watcher import ChangeMonitor, relevant_path

API = 'https://abrehamrahi.ir/api/'
DEFAULT_EXCLUDES = ['.git', 'node_modules', '.venv', 'venv', '__pycache__',
                    '*.tmp', '*.part', '*.lock', '*-wal', '*-shm', '.env', '.env.*',
                    '*.pem', '*.key', 'credentials', '.backup', '.abr-hamrahi-api']


class BackupError(Exception):
    def __init__(self, message, status=400, captcha=False):
        super().__init__(message)
        self.status = status
        self.captcha = captcha


def now():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def atomic_bytes(path, value):
    """Replace on the same filesystem; a failed write leaves the old file intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '-', suffix='.tmp', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))


def protect(data, decrypt=False):
    """Windows DPAPI, tied to the current Windows user. Never plaintext fallback."""
    if os.name != 'nt':
        raise BackupError('نگهداری امن نشست به Windows DPAPI نیاز دارد.')

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    crypt = ctypes.windll.crypt32
    method = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    if not method(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise BackupError('بازکردن یا ذخیرهٔ امن نشست ویندوز ناموفق بود.')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Segment:
    def __init__(self, path, offset, size, cancelled=lambda: None):
        self.path, self.offset, self.size = path, offset, size
        self.cancelled = cancelled

    def __iter__(self):
        with self.path.open('rb') as stream:
            stream.seek(self.offset)
            left = self.size
            while left:
                self.cancelled()
                block = stream.read(min(1024 * 1024, left))
                if not block:
                    raise BackupError('اندازهٔ فایل موقت تغییر کرده است.')
                left -= len(block)
                yield block


def check_transfer_url(url):
    if not isinstance(url, str):
        raise BackupError('ساختار نشانی انتقال معتبر نیست.', 502)
    parsed = urllib.parse.urlsplit(url)
    try:
        valid_port = parsed.port in (None, 443)
    except ValueError:
        valid_port = False
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or not valid_port or parsed.fragment:
        raise BackupError('نشانی انتقال امن و معتبر نیست.')
    try:
        port = parsed.port or 443
        first = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)}
        second = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)}
        if not first or first != second or any(not ipaddress.ip_address(address).is_global for address in first):
            raise BackupError('نشانی انتقال به شبکهٔ عمومی تعلق ندارد.')
    except OSError:
        raise BackupError('نام میزبان انتقال قابل دسترسی نیست.') from None


class HamrahiClient:
    def __init__(self, state_dir, timeout=60):
        self.state_dir = state_dir
        self.timeout = timeout
        self.lock = threading.RLock()
        self.opener = urllib.request.build_opener(NoRedirect())
        self.tokens = {}
        saved = state_dir / 'session.bin'
        self.session_error = None
        if saved.exists():
            try:
                tokens = json.loads(protect(saved.read_bytes(), decrypt=True))
                self.validate_tokens(tokens)
                self.tokens = tokens
            except Exception:
                self.session_error = 'نشست ذخیره‌شده قابل بازکردن نیست؛ دوباره وارد شوید.'

    @staticmethod
    def validate_tokens(value):
        if not isinstance(value, dict) or any(not isinstance(value.get(k), str) or not value[k] or len(value[k]) > 32768 for k in ('access', 'refresh')):
            raise BackupError('پاسخ ورود با ساختار شناخته‌شدهٔ سرویس سازگار نیست.')

    def save_tokens(self, value):
        self.validate_tokens(value)
        tokens = {k: value[k] for k in ('access', 'refresh')}
        data = protect(json.dumps(tokens).encode())
        self.state_dir.mkdir(parents=True, exist_ok=True)
        atomic_bytes(self.state_dir / 'session.bin', data)
        self.tokens = tokens
        self.session_error = None

    def forget(self):
        with self.lock:
            self.tokens = {}
            (self.state_dir / 'session.bin').unlink(missing_ok=True)

    def _json(self, method, path, payload=None, auth=True, retry=True):
        # Retry only reads. An ambiguous POST is never blindly replayed.
        for attempt in range(3):
            try:
                return self._json_once(method, path, payload, auth, retry)
            except BackupError as error:
                if method != 'GET' or error.status not in (429, 502) or attempt == 2:
                    raise
                time.sleep(attempt + 1)

    def _json_once(self, method, path, payload=None, auth=True, retry=True):
        if not re.fullmatch(r'v\d+/[a-zA-Z0-9_/?=&.%-]+', path):
            raise BackupError('مسیر API نامعتبر است.')
        with self.lock:
            if auth and not self.tokens:
                raise BackupError('ابتدا در حساب ابرهمراهی وارد شوید.', 401)
            headers = {'Accept-Language': 'fa', 'user-device': 'web-desktop',
                       'Accept': 'application/json', 'Content-Type': 'application/json'}
            if auth:
                headers['Authorization'] = 'Bearer ' + self.tokens['access']
            body = None if payload is None else json.dumps(payload).encode()
            req = urllib.request.Request(API + path, data=body, headers=headers, method=method)
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise BackupError('پاسخ API بیش از حد مجاز بزرگ است.')
                result = json.loads(raw) if raw else {}
                if not isinstance(result, dict):
                    raise BackupError('ساختار پاسخ API تغییر کرده است؛ عملیات تأیید نشد.', 503)
                return result
            except urllib.error.HTTPError as error:
                try:
                    result = json.loads(error.read(128 * 1024))
                except (ValueError, OSError):
                    result = {}
                finally:
                    error.close()
                if not isinstance(result, dict):
                    result = {}
                if error.code == 401 and auth and retry:
                    self.refresh()
                    return self._json_once(method, path, payload, auth, retry=False)
                if result.get('captcha_required'):
                    raise BackupError('سرویس کپچا می‌خواهد؛ تصویر را دریافت و پاسخ را دستی وارد کنید.', 400, True) from None
                messages = {401: 'نشست معتبر نیست؛ دوباره وارد شوید.',
                            403: 'سرویس اجازهٔ این عملیات را نداد.',
                            404: 'شیء ابری یا مسیر API پیدا نشد.',
                            409: 'نام فایل یا پوشه در مقصد تعارض دارد.',
                            413: 'فضای حساب یا اندازهٔ مجاز فایل کافی نیست.',
                            429: 'تعداد درخواست‌ها زیاد است؛ کمی بعد دوباره تلاش کنید.'}
                raise BackupError(messages.get(error.code, 'پاسخ ناموفق ابرهمراهی؛ HTTP ' + str(error.code)),
                                  502 if error.code >= 500 else error.code) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise BackupError('ارتباط با ابرهمراهی برقرار نشد یا زمان درخواست تمام شد.', 502) from None
            except (ValueError, TypeError):
                raise BackupError('پاسخ API قابل شناسایی نیست؛ اتصال آزمایشی نیاز به بررسی دارد.', 502) from None

    def refresh(self):
        with self.lock:
            try:
                result = self._json('POST', 'v2/profile/auth/token-refresh/',
                                    {'refresh': self.tokens.get('refresh', '')}, auth=False)
            except BackupError as error:
                if error.status == 401:
                    self.forget()
                raise
            self.save_tokens({'access': result.get('access'),
                              'refresh': result.get('refresh') or self.tokens.get('refresh')})

    @staticmethod
    def phone(value):
        value = str(value).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))
        value = value.replace(' ', '').removeprefix('+98').removeprefix('0098').removeprefix('0')
        if not re.fullmatch('9[0-9]{9}', value):
            raise BackupError('شمارهٔ همراه را درست وارد کنید.')
        return value

    def auth(self, action, data):
        with self.lock:
            if action == 'captcha':
                result = self._json('GET', 'v1/captcha/get-captcha/', auth=False)
                image = result.get('image', '')
                if not isinstance(image, str) or len(image) > 1024 * 1024:
                    raise BackupError('تصویر کپچا قابل شناسایی نیست.')
                return {k: result.get(k) for k in ('id', 'provider', 'image')}
            payload = {'phone': self.phone(data.get('phone', '')), 'prefix': '+98'}
            for key in ('captcha_id', 'captcha_value', 'captcha_provider'):
                if data.get(key):
                    payload[key] = data[key]
            if action == 'code':
                self._json('POST', 'v6/profile/auth/generate-code/', payload, auth=False)
                return {'message': 'کد ورود ارسال شد.'}
            if action != 'login':
                raise BackupError('عملیات ورود نامعتبر است.')
            if data.get('code'):
                code = str(data['code']).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789'))
                if not re.fullmatch('[0-9]{6}', code):
                    raise BackupError('کد ورود باید شش رقم باشد.')
                self._json('POST', 'v6/profile/auth/validate-otp/',
                           {k: payload[k] for k in ('phone', 'prefix')} | {'code': code}, auth=False)
                payload['code'] = code
            elif data.get('password'):
                payload['password'] = str(data['password'])
            else:
                raise BackupError('رمز یا کد ورود را وارد کنید.')
            payload['country'] = 'IR'
            result = self._json('POST', 'v6/profile/auth/login/', payload, auth=False)
            if result.get('is_registered'):
                raise BackupError('ابتدا ثبت‌نام و پذیرش مقررات را در سایت رسمی کامل کنید.')
            self.save_tokens(result)
            return {'message': 'ورود انجام شد؛ نشست با حساب ویندوز شما رمز شده است.'}

    def connection(self):
        profile = self._json('GET', 'v6/profile/auth/get-profile/')
        quota = self._json('GET', 'v5/rgw/get-quota/')
        if not isinstance(profile, dict) or not isinstance(quota, dict) or 'used' not in quota:
            raise BackupError('ساختار اطلاعات حساب تغییر کرده است.')
        return {'checkedAt': now(), 'used': int(quota['used']),
                'total': int(quota.get('total_plan', 0)) + int(quota.get('total_gift', 0))}

    def list_folder(self, parent=None):
        items, offset = [], 0
        while True:
            params = {'is_trash': 'false', 'recursive': 'false', 'limit': 1000, 'offset': offset}
            if parent is not None:
                params['parent'] = parent
            result = self._json('GET', 'v2/flat/list-objects/?' + urllib.parse.urlencode(params))
            if not isinstance(result, dict) or not isinstance(result.get('results'), list):
                raise BackupError('ساختار فهرست پوشه‌های ابر تغییر کرده است.')
            batch = result['results']
            if any(not isinstance(x, dict) or not isinstance(x.get('name'), str) or type(x.get('id')) is not int for x in batch):
                raise BackupError('ساختار اعضای پوشهٔ ابری معتبر نیست.', 503)
            items.extend(batch)
            if not result.get('next'):
                return items
            if not batch or offset >= 1000000:
                raise BackupError('صفحه‌بندی فهرست ابری معتبر نیست.')
            offset += 1000

    def folder(self, name, parent, cache):
        key = (parent, name)
        if key in cache:
            return cache[key]
        matches = [x for x in self.list_folder(parent) if x.get('name') == name]
        if len(matches) > 1 or (matches and matches[0].get('key')):
            raise BackupError('نام پوشهٔ مقصد تعارض دارد: ' + name)
        result = matches[0] if matches else self._json('POST', 'v2/flat/create-folder/', {'name': name, 'parent': parent})
        if not isinstance(result.get('id'), int):
            raise BackupError('شناسهٔ پوشهٔ ابری معتبر نیست.')
        cache[key] = result['id']
        return result['id']

    def upload(self, path, name, parent, progress=lambda value: None, cancelled=lambda: None):
        cancelled()
        size = path.stat().st_size
        info = self._json('POST', 'v2/flat/start-upload/', {'obj_size': size})
        urls, chunk = info.get('signed_urls'), info.get('chunk_size')
        if (not isinstance(urls, list) or not urls or not isinstance(chunk, int) or chunk <= 0
                or len(urls) != max(1, math.ceil(size / chunk)) or not info.get('key') or not info.get('upload_id')):
            raise BackupError('قرارداد آپلود با نسخهٔ شناخته‌شده سازگار نیست.')
        parts = []
        for index, url in enumerate(urls):
            cancelled()
            check_transfer_url(url)
            part_size = min(chunk, max(0, size - index * chunk))
            etag = None
            for attempt in range(3):
                cancelled()
                req = urllib.request.Request(url, data=Segment(path, index * chunk, part_size, cancelled),
                                             headers={'Content-Length': str(part_size), 'Content-Type': 'application/octet-stream'}, method='PUT')
                try:
                    with self.opener.open(req, timeout=self.timeout) as response:
                        etag = response.headers.get('ETag')
                    break
                except (urllib.error.URLError, TimeoutError, OSError) as error:
                    permanent = isinstance(error, urllib.error.HTTPError) and error.code < 500 and error.code != 429
                    if isinstance(error, urllib.error.HTTPError):
                        error.close()
                    if attempt == 2 or permanent:
                        raise BackupError('ارسال بخشی از فایل ناموفق بود؛ فایل موفق ثبت نشد.', 502) from None
                    time.sleep(attempt + 1)
            if not etag:
                raise BackupError('رسید ETag از محل ذخیره دریافت نشد؛ ارسال تأیید نشد.')
            parts.append({'ETag': etag, 'PartNumber': index + 1, 'size': part_size})
            progress(min(size, (index + 1) * chunk))
        cancelled()
        result = self._json('POST', 'v2/flat/complete-upload/',
                            {'key': info['key'], 'name': name, 'parent': parent,
                             'upload_id': info['upload_id'], 'parts': parts, 'force_overwrite': False})
        if not isinstance(result, dict) or not isinstance(result.get('id'), int):
            raise BackupError('تأیید نهایی فایل ابری قابل شناسایی نیست.')
        if int(result.get('size', -1)) != size:
            raise BackupError('اندازهٔ فایل ابری با فایل ارسالی برابر نیست.')
        return result

    def verify(self, object_id, expected_hash, expected_size, cancelled=lambda: None):
        cancelled()
        result = self._json('GET', f'v2/flat/get-object/{int(object_id)}/')
        url = result.get('download_url')
        if not isinstance(url, str):
            raise BackupError('نشانی بازیابی فایل دریافت نشد.')
        check_transfer_url(url)
        # Bearer credentials are only sent to the fixed API origin.
        headers = {}
        if urllib.parse.urlsplit(url).netloc == 'abrehamrahi.ir':
            headers['Authorization'] = 'Bearer ' + self.tokens.get('access', '')
        digest, length = hashlib.sha256(), 0
        try:
            with self.opener.open(urllib.request.Request(url, headers=headers), timeout=self.timeout) as response:
                while True:
                    cancelled()
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    length += len(block)
                    if length > expected_size:
                        raise BackupError('اندازهٔ بازیابی‌شده با فایل اصلی برابر نیست.')
                    digest.update(block)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise BackupError('دریافت مجدد فایل برای آزمون صحت ناموفق بود.', 502) from None
        if length != expected_size or digest.hexdigest() != expected_hash:
            raise BackupError('هش SHA-256 فایل بازیابی‌شده با فایل اصلی برابر نیست.')


def is_link(path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def cloud_name(value):
    value = re.sub(r'[+*?^$\[\]{}|/\\~#@!<>:"\x00-\x1f]', '_', value).strip(' .')
    if not value:
        raise BackupError('نام مقصد معتبر نیست.')
    return value[:180]


class BackupManager:
    def __init__(self, base, client=None, state_dir=None):
        self.base = Path(base).resolve()
        self.state_dir = Path(state_dir).resolve() if state_dir else self.base / '.backup'
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config_path = self.state_dir / 'config.json'
        self.lock = threading.RLock()
        self.work_lock = threading.Lock()
        self.stop = threading.Event()
        self.shutdown = threading.Event()
        self.browse_lock = threading.Lock()
        self.change_due = None
        self.change_generation = 0
        self.synced_generation = 0
        self.retry_at = 0
        self.retry_attempt = 0
        self.change_monitor = ChangeMonitor(self._on_source_change)
        self.client = client or HamrahiClient(self.state_dir)
        self.state = {'busy': False, 'action': None, 'phase': 'آماده', 'current': '',
                      'uploaded': 0, 'skipped': 0, 'bytes': 0, 'errors': [], 'lastResult': None,
                      'connection': None, 'lastTest': None, 'nextRun': None, 'pendingSync': False,
                      'watching': 0, 'watchBackend': None, 'watchError': None, 'lastChange': None,
                      'nextSync': None, 'trigger': None}
        self.config_error = None
        self.config = {'version': 1, 'remoteRoot': 'Abr-Hamrahi-API', 'intervalMinutes': 60,
                       'automatic': False, 'syncOnChange': True, 'automaticPaused': False,
                       'debounceSeconds': 5, 'maxFileMiB': 1024, 'settleSeconds': 15,
                       'verifyUploads': True, 'exclude': DEFAULT_EXCLUDES[:], 'folders': []}
        try:
            if self.config_path.exists():
                self.config = self.validate_config(json.loads(self.config_path.read_text('utf-8')))
        except Exception:
            self.config_error = 'تنظیمات ذخیره‌شده معتبر نیست؛ اجرای خودکار متوقف است.'
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS files (job TEXT, rel TEXT, sha TEXT, size INTEGER, mtime INTEGER, remote_id INTEGER, remote_name TEXT, PRIMARY KEY(job,rel))')
        try:
            self.history = json.loads((self.state_dir / 'history.json').read_text('utf-8'))[:30]
            if self.history:
                self.state['lastResult'] = self.history[0]
        except (OSError, ValueError):
            self.history = []
        if self.client.tokens:
            try:
                self.state['lastTest'] = json.loads((self.state_dir / 'last-test.json').read_text('utf-8'))
            except (OSError, ValueError):
                pass
        if self.config['automatic']:
            self._queue_sync()

    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.state_dir / 'index.sqlite3', timeout=20)) as connection:
            with connection:
                yield connection

    def validate_config(self, data):
        if not isinstance(data, dict):
            raise BackupError('تنظیمات باید یک شیء باشد.')
        if type(data.get('version', 1)) is not int or data.get('version', 1) != 1:
            raise BackupError('نسخهٔ تنظیمات پشتیبانی نمی‌شود؛ فایل اصلی حفظ شد.')
        result = {'version': 1, 'remoteRoot': cloud_name(str(data.get('remoteRoot', 'Abr-Hamrahi-API')))}
        for key, default, low, high in [('intervalMinutes', 60, 5, 10080), ('maxFileMiB', 1024, 1, 20480),
                                      ('settleSeconds', 15, 0, 3600), ('debounceSeconds', 5, 1, 300)]:
            if type(data.get(key, default)) is not int:
                raise BackupError('تنظیم عددی باید عدد صحیح باشد: ' + key)
            try:
                number = int(data.get(key, default))
            except (ValueError, TypeError):
                raise BackupError('تنظیم عددی معتبر نیست: ' + key)
            if not low <= number <= high:
                raise BackupError('تنظیم عددی خارج از بازه است: ' + key)
            result[key] = number
        result['automatic'] = data.get('automatic', False) is True
        result['syncOnChange'] = data.get('syncOnChange', True) is True
        result['automaticPaused'] = data.get('automaticPaused', False) is True
        result['verifyUploads'] = data.get('verifyUploads', True) is True
        excludes = data.get('exclude', DEFAULT_EXCLUDES)
        if not isinstance(excludes, list) or len(excludes) > 200 or any(not isinstance(x, str) or len(x) > 250 for x in excludes):
            raise BackupError('فهرست استثناها معتبر نیست.')
        result['exclude'] = [x.strip() for x in excludes if x.strip()]
        folders = data.get('folders', [])
        if not isinstance(folders, list) or len(folders) > 100:
            raise BackupError('حداکثر صد پوشه می‌توانید اضافه کنید.')
        result['folders'], seen, ids = [], [], set()
        for item in folders:
            if not isinstance(item, dict):
                raise BackupError('تنظیم پوشه معتبر نیست.')
            raw = str(item.get('path', '')).strip()
            path = Path(raw)
            if not raw or not path.is_absolute() or raw.startswith(('\\\\', '//')):
                raise BackupError('مسیر پوشه باید کامل و محلی باشد.')
            resolved = path.resolve()
            if any(p.exists() and is_link(p) for p in (path, *path.parents)):
                raise BackupError('پوشهٔ پیوندی قابل انتخاب نیست.')
            if resolved == Path(resolved.anchor) or within(resolved, self.state_dir):
                raise BackupError('ریشهٔ دیسک و محل تنظیمات پشتیبان قابل انتخاب نیست.')
            if path.exists() and not path.is_dir():
                raise BackupError('مسیر انتخابی پوشه نیست.')
            if any(within(resolved, old) or within(old, resolved) for old in seen):
                raise BackupError('پوشه‌های هم‌پوشان یا تکراری را جداگانه انتخاب نکنید.')
            seen.append(resolved)
            job_id = item.get('id') or uuid.uuid4().hex[:12]
            if not isinstance(job_id, str) or not re.fullmatch('[a-f0-9]{12}', job_id) or job_id in ids:
                raise BackupError('شناسهٔ پوشه معتبر نیست.')
            ids.add(job_id)
            result['folders'].append({'id': job_id, 'path': str(resolved),
                                      'name': cloud_name(str(item.get('name') or path.name)),
                                      'enabled': item.get('enabled', True) is True})
        return result

    def save_config(self, data):
        if not self.work_lock.acquire(False):
            raise BackupError('تا پایان کار جاری صبر کنید.', 409)
        try:
            return self._save_config(data)
        finally:
            self.work_lock.release()

    def _save_config(self, data):
        with self.lock:
            if self.state['busy']:
                raise BackupError('تا پایان کار جاری صبر کنید.', 409)
            result = self.validate_config(data)
            atomic_json(self.config_path, result)
            self.config = result
            self.config_error = None
            self.state['nextRun'] = time.time() + result['intervalMinutes'] * 60 if result['automatic'] else None
            self.retry_at = 0
            self.retry_attempt = 0
            if result['automatic']:
                self._queue_sync()
            else:
                self.change_due = None
                self.synced_generation = self.change_generation
            return self.public()

    def public(self):
        with self.lock:
            self._refresh_automation()
            return json.loads(json.dumps({'config': self.config, 'state': self.state, 'history': self.history,
                                          'authenticated': bool(self.client.tokens),
                                          'error': self.config_error or self.client.session_error,
                                          'provider': {'experimental': True, 'checkedOn': '2026-11-04',
                                                       'mode': 'one-way-versioned'}}))

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def _on_source_change(self, path):
        with self.lock:
            if not self.config.get('automatic') or not self.config.get('syncOnChange'):
                return
            if relevant_path(path, self.config, self.state_dir) is None:
                return
            self._queue_sync()
            self.state['lastChange'] = now()

    def configure_change_monitor(self):
        with self.lock:
            config = json.loads(json.dumps(self.config))
        status = self.change_monitor.configure(config, self.state_dir)
        with self.lock:
            if status['watching'] and status['watching'] != self.state['watching']:
                self._queue_sync()
            self.state.update(watching=status['watching'], watchBackend=status['backend'], watchError=status['error'])

    def _queue_sync(self):
        self.change_generation += 1
        self.change_due = time.time() + max(self.config['debounceSeconds'], self.config['settleSeconds'])

    def _refresh_automation(self):
        pending = self.change_generation > self.synced_generation
        self.state['pendingSync'] = pending
        deadlines = [deadline for deadline in (self.change_due if pending else None, self.state['nextRun'])
                     if deadline is not None]
        ready = (self.config['automatic'] and not self.config['automaticPaused'] and
                 bool(self.client.tokens) and not self.config_error and not self.state['busy'])
        self.state['nextSync'] = max(min(deadlines), self.retry_at) if ready and deadlines else None

    def start(self, action, trigger='manual'):
        if action not in ('preview', 'sync', 'test', 'connection'):
            raise BackupError('عملیات معتبر نیست.')
        if not self.work_lock.acquire(False):
            raise BackupError('یک کار دیگر در حال اجراست.', 409)
        with self.lock:
            if self.shutdown.is_set() or (trigger != 'manual' and
                    (not self.config['automatic'] or self.config['automaticPaused'] or not self.client.tokens)):
                self.work_lock.release()
                raise BackupError('اجرای خودکار اکنون آماده نیست.', 409)
            generation = self.change_generation
            self.stop.clear()
            self.state.update(busy=True, action=action, trigger=trigger, phase='شروع بررسی', current='',
                              uploaded=0, skipped=0, bytes=0, errors=[])
        if action != 'preview':
            self.update(connection=None)
        def worker():
            succeeded = False
            try:
                if action == 'connection':
                    self.update(connection=self.client.connection(), phase='اتصال حساب تأیید شد')
                elif action == 'test':
                    self.test_roundtrip()
                else:
                    self.sync(preview=action == 'preview', verify_existing=trigger != 'change')
                    succeeded = bool(self.state['lastResult'] and self.state['lastResult']['ok'])
            except Exception as error:
                message = str(error) if isinstance(error, BackupError) else 'عملیات ناموفق بود؛ فایل موفق ثبت نشد. نوع خطا: ' + type(error).__name__
                self.update(phase='عملیات ناموفق', errors=[message])
                if action == 'test':
                    self.update(lastTest={'at': now(), 'ok': False, 'message': message})
                if action in ('sync', 'preview'):
                    with self.lock:
                        failed = {'at': now(), 'completedAt': now(), 'preview': action == 'preview', 'ok': False,
                                  'uploaded': self.state['uploaded'], 'unchanged': self.state['skipped'],
                                  'bytes': self.state['bytes'], 'errors': [message], 'samples': [],
                                  'excluded': 0, 'files': 0, 'manifests': []}
                        self.history.insert(0, failed)
                        self.history = self.history[:30]
                        self.state['lastResult'] = failed
                        atomic_json(self.state_dir / 'history.json', self.history)
            finally:
                with self.lock:
                    if action == 'sync':
                        if trigger != 'change' or self.state['nextRun'] is None:
                            self.state['nextRun'] = time.time() + self.config['intervalMinutes'] * 60 if self.config['automatic'] else None
                        if succeeded:
                            self.synced_generation = max(self.synced_generation, generation)
                            self.retry_at = 0
                            self.retry_attempt = 0
                            if self.change_generation <= self.synced_generation:
                                self.change_due = None
                        elif self.config['automatic']:
                            if self.change_generation <= self.synced_generation:
                                self._queue_sync()
                            self.retry_attempt = min(7, self.retry_attempt + 1)
                            self.retry_at = time.time() + min(300, 5 * 2 ** (self.retry_attempt - 1))
                    self.state.update(busy=False, current='')
                    self._refresh_automation()
                self.work_lock.release()
        threading.Thread(target=worker, daemon=True, name='cloud-backup').start()
        return {'started': True}

    def cancelled(self):
        if self.stop.is_set():
            raise BackupError('کار با درخواست شما متوقف شد؛ فایل‌های موفق حفظ شدند.')

    def walk(self, root, config, errors, skips):
        patterns = [x.replace('\\', '/').lower() for x in config['exclude']]
        def excluded(path):
            rel = path.relative_to(root).as_posix().lower()
            return (within(path.resolve(), self.state_dir) or
                    any(fnmatch.fnmatchcase(rel, p) or fnmatch.fnmatchcase(path.name.lower(), p) for p in patterns))
        def visit(directory):
            self.cancelled()
            try:
                entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                errors.append('دسترسی به پوشه ممکن نیست: ' + str(directory))
                return
            for path in entries:
                self.cancelled()
                try:
                    if is_link(path) or excluded(path):
                        skips.append(str(path))
                    elif path.is_dir():
                        yield from visit(path)
                    elif path.is_file():
                        info = path.stat()
                        if info.st_size > config['maxFileMiB'] * 1048576:
                            errors.append('فایل از سقف اندازه بزرگ‌تر است: ' + str(path))
                        elif time.time() - info.st_mtime < config['settleSeconds']:
                            errors.append('فایل اخیراً تغییر کرده؛ در نوبت بعد دوباره بررسی می‌شود: ' + str(path))
                        elif path.suffix.lower() in ('.mdf', '.ldf', '.ndf'):
                            errors.append('برای SQL Server خروجی بکاپ .bak تهیه و آن را انتخاب کنید: ' + str(path))
                        else:
                            yield path
                except OSError:
                    errors.append('دسترسی به فایل ممکن نیست: ' + str(path))
        if not root.is_dir():
            errors.append('پوشه در دسترس نیست: ' + str(root))
            return
        if is_link(root):
            errors.append('پوشه به پیوند تبدیل شده است: ' + str(root))
            return
        yield from visit(root)

    def snapshot(self, path, target):
        if any(is_link(p) for p in (path, *path.parents)):
            raise BackupError('مسیر فایل پیوندی شده است؛ ارسال متوقف شد.')
        before = path.stat()
        if shutil.disk_usage(target.parent).free < before.st_size + 16 * 1024 * 1024:
            raise BackupError('فضای محلی کافی برای snapshot امن وجود ندارد.')
        with path.open('rb') as stream:
            sqlite = stream.read(16) == b'SQLite format 3\x00'
        if sqlite:
            source = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
            deadline = time.monotonic() + 45
            try:
                with closing(sqlite3.connect(target)) as destination:
                    def progress(status, remaining, total):
                        self.cancelled()
                        if time.monotonic() > deadline:
                            raise BackupError('ساخت snapshot پایگاه‌داده بیش از زمان مجاز طول کشید.')
                    source.backup(destination, pages=256, progress=progress)
            finally:
                source.close()
        else:
            with path.open('rb') as source, target.open('wb') as output:
                while True:
                    self.cancelled()
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise BackupError('فایل هنگام خواندن تغییر کرده است: ' + str(path))
        if any(is_link(p) for p in (path, *path.parents)):
            raise BackupError('مسیر فایل حین خواندن پیوندی شده است؛ ارسال متوقف شد.')
        digest = hashlib.sha256()
        with target.open('rb') as stream:
            while True:
                self.cancelled()
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        return digest.hexdigest(), target.stat().st_size, before.st_mtime_ns, sqlite

    def sync(self, preview=False, verify_existing=True):
        config = json.loads(json.dumps(self.config))
        folders = [x for x in config['folders'] if x['enabled']]
        if not folders:
            raise BackupError('حداقل یک پوشهٔ فعال اضافه کنید.')
        connection = None if preview else self.client.connection()
        if connection:
            self.update(connection=connection)
        remaining = max(0, connection['total'] - connection['used']) if connection else None
        result = {'at': now(), 'preview': preview, 'uploaded': 0, 'unchanged': 0, 'bytes': 0,
                  'files': 0, 'errors': [], 'excluded': 0, 'samples': [], 'manifests': []}
        folders_cache, remote_cache = {}, {}
        def remote_parent(job, relative):
            parent = self.client.folder(config['remoteRoot'], None, folders_cache)
            parent = self.client.folder(job['name'] + '-' + job['id'], parent, folders_cache)
            for part in relative.parts[:-1]:
                # Hash suffix keeps different local names distinct after sanitizing.
                parent = self.client.folder(cloud_name(part) + '-' + hashlib.sha256(part.encode()).hexdigest()[:8], parent, folders_cache)
            return parent
        with tempfile.TemporaryDirectory(prefix='staging-', dir=self.state_dir) as staging:
            staged = Path(staging) / 'file'
            for job in folders:
                manifest = []
                errors, skips = result['errors'], []
                root = Path(job['path'])
                for path in self.walk(root, config, errors, skips):
                    relative = path.relative_to(root)
                    self.update(phase='پیش‌نمایش و محاسبهٔ هش' if preview else 'آماده‌سازی و ارسال', current=str(path))
                    try:
                        digest, size, mtime, is_sqlite = self.snapshot(path, staged)
                        if size > config['maxFileMiB'] * 1048576:
                            raise BackupError('snapshot از سقف اندازه بزرگ‌تر است: ' + str(path))
                        with self.db() as db:
                            old = db.execute('SELECT sha,remote_id,remote_name FROM files WHERE job=? AND rel=?', (job['id'], relative.as_posix())).fetchone()
                        suffix = '.' + cloud_name(path.suffix[1:]) if path.suffix else ''
                        filename = cloud_name(path.stem)[:120] + '__' + digest[:20] + suffix
                        object_id, exists = None, False
                        if not preview:
                            parent = remote_parent(job, relative)
                            if parent not in remote_cache:
                                listing = self.client.list_folder(parent)
                                names = [item['name'] for item in listing]
                                if len(names) != len(set(names)):
                                    raise BackupError('نام تکراری در پوشهٔ ابری وجود دارد؛ انتخاب خودکار امن نیست.')
                                remote_cache[parent] = dict(zip(names, listing))
                            remote = remote_cache[parent].get(filename)
                            if remote:
                                if int(remote.get('size', -1)) != size or not remote.get('key'):
                                    raise BackupError('نسخهٔ ابری هم‌نام با اندازهٔ ناسازگار وجود دارد: ' + str(relative))
                                object_id, exists = remote['id'], True
                                if (verify_existing and config['verifyUploads']) or not old or old[0] != digest or old[1] != object_id:
                                    self.client.verify(object_id, digest, size, cancelled=self.cancelled)
                            else:
                                self.cancelled()
                                if remaining is not None and size > remaining:
                                    raise BackupError('ظرفیت باقی‌ماندهٔ حساب برای این فایل کافی نیست.', 413)
                                uploaded = self.client.upload(staged, filename, parent, cancelled=self.cancelled)
                                if remaining is not None:
                                    remaining -= size
                                object_id = uploaded['id']
                                if config['verifyUploads']:
                                    self.update(phase='دریافت مجدد و بررسی SHA-256', current=str(path))
                                    self.client.verify(object_id, digest, size, cancelled=self.cancelled)
                                remote_cache[parent][filename] = uploaded
                        else:
                            exists = bool(old and old[0] == digest)
                        entry = {'path': relative.as_posix(), 'sha256': digest, 'size': size,
                                 'remoteId': object_id, 'remoteName': filename, 'sqliteSnapshot': is_sqlite}
                        manifest.append(entry)
                        result['files'] += 1
                        if exists:
                            result['unchanged'] += 1
                        else:
                            result['uploaded'] += 1
                            result['bytes'] += size
                        if len(result['samples']) < 100:
                            result['samples'].append({'path': str(path), 'bytes': size, 'changed': not exists})
                        if not preview:
                            with self.db() as db:
                                db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)',
                                           (job['id'], relative.as_posix(), digest, size, mtime, object_id, filename))
                        self.update(uploaded=result['uploaded'], skipped=result['unchanged'], bytes=result['bytes'])
                    except (OSError, sqlite3.Error, BackupError) as error:
                        if self.stop.is_set():
                            raise
                        if isinstance(error, BackupError):
                            errors.append(str(error))
                            if error.status in (401, 403, 429, 502):
                                raise
                        else:
                            errors.append('خواندن یا snapshot فایل ناموفق بود: ' + str(path))
                    finally:
                        staged.unlink(missing_ok=True)
                result['excluded'] += len(skips)
                if not preview:
                    self.cancelled()
                    self.update(phase='ذخیرهٔ فهرست بازیابی', current=job['path'])
                    manifest_file = Path(staging) / 'manifest.json'
                    atomic_json(manifest_file, {'schema': 1, 'at': result['at'],
                                                'folder': job['name'], 'files': manifest, 'errorCount': len(errors),
                                                'exclude': config['exclude'], 'mode': 'one-way-versioned'})
                    name = '_manifest-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.json'
                    parent = remote_parent(job, Path('manifest.json'))
                    obj = self.client.upload(manifest_file, name, parent, cancelled=self.cancelled)
                    if config['verifyUploads']:
                        self.client.verify(obj['id'], hashlib.sha256(manifest_file.read_bytes()).hexdigest(), manifest_file.stat().st_size, cancelled=self.cancelled)
                    result['manifests'].append({'folder': job['name'], 'remoteId': obj['id'], 'name': name})
        result['ok'] = not result['errors']
        result['completedAt'] = now()
        with self.lock:
            self.history.insert(0, result)
            self.history = self.history[:30]
            atomic_json(self.state_dir / 'history.json', self.history)
            self.state.update(lastResult=result, errors=result['errors'],
                              phase=('پیش‌نمایش آماده است' if preview else 'پشتیبان‌گیری پایان یافت') + ('؛ نیاز به بررسی خطاها' if result['errors'] else ''))

    def test_roundtrip(self):
        self.update(phase='بررسی اتصال حساب')
        self.update(connection=self.client.connection())
        content = ('Abre Hamrahi API self-test\n' + now() + '\n' + uuid.uuid4().hex + '\n').encode()
        digest = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory(prefix='selftest-', dir=self.state_dir) as staging:
            path = Path(staging) / 'test.txt'
            path.write_bytes(content)
            root = self.client.folder(self.config['remoteRoot'], None, {})
            folder = self.client.folder('_connection-tests', root, {})
            name = 'test-' + uuid.uuid4().hex[:16] + '.txt'
            self.update(phase='آپلود فایل آزمون')
            result = self.client.upload(path, name, folder, cancelled=self.cancelled)
            self.update(phase='بازیابی و مقایسهٔ هش فایل آزمون')
            self.client.verify(result['id'], digest, len(content), cancelled=self.cancelled)
        test = {'at': now(), 'ok': True, 'sha256': digest, 'size': len(content), 'remoteId': result['id'],
                'message': 'آپلود و بازیابی با SHA-256 یکسان تأیید شد. فایل کوچک آزمون در ابر باقی ماند.'}
        atomic_json(self.state_dir / 'last-test.json', test)
        self.update(lastTest=test, phase='آزمون واقعی آپلود و بازیابی موفق بود')

    def scheduler(self):
        try:
            while not self.shutdown.is_set():
                self.configure_change_monitor()
                self.scheduler_tick()
                self.shutdown.wait(1)
        finally:
            self.change_monitor.close()

    def scheduler_tick(self):
        with self.lock:
            if self.config['automatic'] and self.state['nextRun'] is None:
                self.state['nextRun'] = time.time() + self.config['intervalMinutes'] * 60
            self._refresh_automation()
            due = self.state['nextSync']
            if self.shutdown.is_set() or due is None or time.time() < due:
                return
            trigger = 'schedule' if self.state['nextRun'] is not None and self.state['nextRun'] <= time.time() else 'change'
        try:
            self.start('sync', trigger=trigger)
        except BackupError:
            pass

    def handle(self, action, data):
        if action == 'config':
            return self.save_config(data)
        if action in ('preview', 'sync', 'test', 'connection'):
            return self.start(action)
        if action in ('pause', 'resume'):
            with self.lock:
                config = self.config | {'automaticPaused': action == 'pause'}
                atomic_json(self.config_path, config)
                self.config = config
                if action == 'resume' and self.config['automatic']:
                    self.retry_at = 0
                    self._queue_sync()
                elif action == 'pause' and self.state['busy'] and self.state['action'] == 'sync':
                    self.stop.set()
            self.configure_change_monitor()
            return {'message': 'سینک خودکار متوقف شد؛ برای ادامه دکمهٔ ادامه را بزنید.' if action == 'pause'
                    else 'سینک خودکار ادامه دارد؛ تغییرات دوباره بررسی می‌شوند.'}
        if action == 'cancel':
            self.stop.set()
            if self.config['automatic']:
                self.handle('pause', {})
            return {'message': 'توقف پس از پایان درخواست جاری اعمال می‌شود.'}
        if action in ('login', 'code', 'captcha', 'logout'):
            if not self.work_lock.acquire(False):
                raise BackupError('تا پایان کار جاری صبر کنید.', 409)
            try:
                if action == 'logout':
                    self.client.forget()
                    with self.lock:
                        self.config['automatic'] = False
                        atomic_json(self.config_path, self.config)
                        self.change_due = None
                        self.synced_generation = self.change_generation
                        self.state.update(connection=None, nextRun=None, lastTest=None, pendingSync=False)
                        (self.state_dir / 'last-test.json').unlink(missing_ok=True)
                    self.configure_change_monitor()
                    return {'message': 'نشست محلی پاک شد؛ اجرای خودکار خاموش است.'}
                result = self.client.auth(action, data)
                if action == 'login':
                    self.update(connection=None, lastTest=None)
                    (self.state_dir / 'last-test.json').unlink(missing_ok=True)
                return result
            finally:
                self.work_lock.release()
        raise BackupError('مسیر یافت نشد.', 404)

    def browse(self):
        if not self.browse_lock.acquire(False):
            raise BackupError('پنجرهٔ انتخاب پوشه از قبل باز است.', 409)
        try:
            return self._browse()
        finally:
            self.browse_lock.release()

    def _browse(self):
        if os.name != 'nt':
            raise BackupError('در این سیستم مسیر پوشه را دستی وارد کنید.')
        script = Path(__file__).with_name('folder_picker.py')
        if not script.is_file():
            raise BackupError('فایل انتخاب‌گر پوشه در نصب برنامه پیدا نشد.')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        executable = Path(sys.executable)
        if executable.name.lower() == 'pythonw.exe':
            executable = executable.with_name('python.exe')
        try:
            result = subprocess.run([str(executable), '-I', str(script)],
                                    capture_output=True, timeout=180, creationflags=flags)
            if result.returncode:
                raise BackupError('پنجرهٔ انتخاب پوشه باز نشد؛ نصب Python باید مؤلفهٔ Tcl/Tk داشته باشد. مسیر را می‌توانید دستی وارد کنید.')
            payload = json.loads(result.stdout.decode('utf-8-sig'))
            if not isinstance(payload, dict) or not isinstance(payload.get('path'), str) or type(payload.get('cancelled')) is not bool:
                raise ValueError()
            path = payload['path'].strip()
            if path and not Path(path).is_dir():
                raise ValueError()
            return {'path': path, 'cancelled': payload['cancelled']}
        except (ValueError, UnicodeError):
            raise BackupError('پاسخ انتخاب‌گر پوشه قابل خواندن نبود.') from None
        except subprocess.TimeoutExpired:
            raise BackupError('مهلت انتخاب پوشه تمام شد.') from None
        except OSError:
            raise BackupError('اجرای انتخاب‌گر پوشه ممکن نشد؛ مسیر پوشه را دستی وارد کنید.') from None

    def close(self):
        self.shutdown.set()
        self.stop.set()
        self.change_monitor.close()
