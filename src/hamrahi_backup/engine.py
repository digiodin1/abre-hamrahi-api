"""One-way, versioned Abrehamrahi backup. No browser session is used.

The provider's private web API was inspected on 2026-11-04. It is not a
published integration contract; reject unexpected responses instead of guessing.
"""
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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
from .startup import StartupRegistration, StartupPathError
from .layout import CloudLayout, METADATA
from .transport import PooledHTTPSOpener
from . import __version__

API = 'https://abrehamrahi.ir/api/'
DEFAULT_EXCLUDES = []


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
    def __init__(self, path, offset, size, cancelled=lambda: None, progress=lambda value: None):
        self.path, self.offset, self.size = path, offset, size
        self.cancelled = cancelled
        self.progress = progress

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
                self.progress(self.size - left)


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
        self.opener = PooledHTTPSOpener(urllib.request.build_opener(NoRedirect()))
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
                with self.lock:
                    if headers.get("Authorization") == "Bearer " + self.tokens.get("access", ""):
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
            raise BackupError('پاسخ API قابل شناسایی نیست؛ اتصال نیاز به بررسی دارد.', 502) from None

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
        if not isinstance(profile, dict):
            raise BackupError('ساختار اطلاعات حساب تغییر کرده است.')
        return self.quota()

    def quota(self, refresh=True):
        quota = self._json('GET', 'v5/rgw/get-quota/') if refresh else self._json_once(
            'GET', 'v5/rgw/get-quota/', retry=False)
        values = [quota.get(key) for key in ('used', 'total_plan', 'total_gift')]
        if any(type(value) is not int or value < 0 for value in values):
            raise BackupError('ساختار ظرفیت حساب معتبر نیست؛ عدد قبلی حفظ شد.', 502)
        return {'checkedAt': now(), 'used': values[0], 'total': values[1] + values[2]}

    def list_folder(self, parent=None):
        return self._list_objects(parent)

    def list_tree(self, parent):
        return self._list_objects(parent, recursive=True)

    def _list_objects(self, parent, recursive=False):
        items, offset = [], 0
        while True:
            params = {'is_trash': 'false', 'recursive': 'true' if recursive else 'false', 'limit': 1000, 'offset': offset}
            if parent is not None:
                params['parent'] = parent
            result = self._json('GET', 'v2/flat/list-objects/?' + urllib.parse.urlencode(params))
            if not isinstance(result, dict) or not isinstance(result.get('results'), list):
                raise BackupError('ساختار فهرست پوشه‌های ابر تغییر کرده است.')
            batch = result['results']
            if any(not isinstance(x, dict) or not isinstance(x.get('name'), str) or type(x.get('id')) is not int for x in batch):
                raise BackupError('ساختار اعضای پوشهٔ ابری معتبر نیست.', 503)
            for item in batch:
                current = item.get('is_current_version', True)
                if type(current) is not bool:
                    raise BackupError('وضعیت نسخهٔ فعلی فایل ابری معتبر نیست.', 503)
                if current:
                    items.append(item)
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
        if result.get('name') != name or result.get('parent') != parent:
            raise BackupError('سرویس نام یا محل پوشه را عیناً حفظ نکرد؛ عملیات تأیید نشد.', 503)
        cache[key] = result['id']
        return result['id']

    def relocate(self, object_id, field, value):
        """Reconcile an ambiguous mutation by reading, never by replaying it."""
        before = self._json('GET', f'v2/flat/get-object/{int(object_id)}/')
        if before.get('id') != object_id:
            raise BackupError('شناسهٔ شیء مقصد قابل تأیید نیست.', 503)
        if before.get(field) == value:
            return before
        endpoint, payload = ('rename-object/', {'obj_id': object_id, 'name': value}) if field == 'name' else (
            'move-object/', {'source_obj_id': object_id, 'target_parent_id': value})
        failure = None
        try:
            self._json('POST', 'v2/rgw/' + endpoint, payload)
        except BackupError as error:
            failure = error
        after = self._json('GET', f'v2/flat/get-object/{int(object_id)}/')
        other = 'parent' if field == 'name' else 'name'
        if (after.get('id') != object_id or after.get(field) != value
                or after.get(other) != before.get(other) or after.get('key') != before.get('key')
                or after.get('size') != before.get('size')):
            raise failure or BackupError('تغییر نام یا انتقال شیء ابری تأیید نشد؛ عملیات متوقف شد.', 503)
        return after

    def rename(self, object_id, name):
        return self.relocate(object_id, 'name', name)

    def move(self, object_id, parent):
        return self.relocate(object_id, 'parent', parent)

    def trash(self, expected):
        object_id = expected['id']
        before = self._json('GET', f'v2/flat/get-object/{object_id}/')
        fields = ('id', 'name', 'parent', 'key', 'size')
        if any(before.get(field) != expected.get(field) for field in fields):
            raise BackupError('فایل ابری پیش از انتقال به سطل زباله تغییر کرده است.', 409)
        if before.get('is_trash') is True:
            return before
        failure = None
        try:
            self._json('DELETE', 'v2/rgw/trash-objects/', {'obj_ids': [object_id]})
        except BackupError as error:
            failure = error
        after = self._json('GET', f'v2/flat/get-object/{object_id}/')
        if any(after.get(field) != expected.get(field) for field in fields) or after.get('is_trash') is not True:
            raise failure or BackupError('انتقال به سطل زباله تأیید نشد.', 503)
        return after

    def upload(self, path, name, parent, progress=lambda value: None, cancelled=lambda: None, replace=None):
        cancelled()
        size = path.stat().st_size
        info = self._json('POST', 'v2/flat/start-upload/', {'obj_size': size})
        urls, chunk = info.get('signed_urls'), info.get('chunk_size')
        if (not isinstance(urls, list) or not urls or not isinstance(chunk, int) or chunk <= 0
                or len(urls) != max(1, math.ceil(size / chunk)) or not info.get('key') or not info.get('upload_id')):
            raise BackupError('قرارداد آپلود با نسخهٔ شناخته‌شده سازگار نیست.')
        sent, progress_lock, aborted = [0] * len(urls), threading.Lock(), threading.Event()
        def send_part(index):
            url = urls[index]
            cancelled()
            check_transfer_url(url)
            part_size = min(chunk, max(0, size - index * chunk))
            def advance(value):
                with progress_lock:
                    sent[index] = max(sent[index], value)
                    progress(sum(sent))
            etag = None
            for attempt in range(3):
                cancelled()
                req = urllib.request.Request(url, data=Segment(path, index * chunk, part_size, cancelled, advance),
                                             headers={'Content-Length': str(part_size), 'Content-Type': 'application/octet-stream'}, method='PUT')
                try:
                    with self.opener.open(req, timeout=self.timeout) as response:
                        etag = response.headers.get('ETag')
                        response.read(65536)
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
            advance(part_size)
            return {'ETag': etag, 'PartNumber': index + 1, 'size': part_size}
        def bounded_part(index):
            if aborted.is_set():
                raise BackupError('ارسال بخش‌های باقیمانده پس از خطا متوقف شد.', 502)
            try:
                return send_part(index)
            except Exception:
                aborted.set()
                raise
        if len(urls) == 1:
            parts = [bounded_part(0)]
        else:
            with ThreadPoolExecutor(max_workers=min(4, len(urls)), thread_name_prefix='cloud-upload') as executor:
                parts = list(executor.map(bounded_part, range(len(urls))))
        cancelled()
        if replace is not None:
            observed = self._json('GET', f'v2/flat/get-object/{replace["id"]}/')
            if (observed.get('is_trash') or any(observed.get(field) != replace.get(field)
                    for field in ('id', 'name', 'parent', 'key', 'size'))):
                raise BackupError('فایل مقصد حین ارسال تغییر کرد؛ جایگزینی انجام نشد.', 409)
        payload = {'key': info['key'], 'name': name, 'parent': parent,
                   'upload_id': info['upload_id'], 'parts': parts, 'force_overwrite': replace is not None}
        if replace is not None and replace.get('version_group') is not None:
            payload['version_group'] = replace['version_group']
        result = self._json('POST', 'v2/flat/complete-upload/',
                            payload)
        if not isinstance(result, dict) or not isinstance(result.get('id'), int):
            raise BackupError('تأیید نهایی فایل ابری قابل شناسایی نیست.')
        if int(result.get('size', -1)) != size:
            raise BackupError('اندازهٔ فایل ابری با فایل ارسالی برابر نیست.')
        if result.get('name') != name or result.get('parent') != parent:
            raise BackupError('سرویس نام یا محل فایل را عیناً حفظ نکرد؛ عملیات تأیید نشد.', 503)
        cancelled()
        if not isinstance(result.get('key'), str) or not result['key']:
            raise BackupError('شناسهٔ ذخیره‌سازی نهایی فایل دریافت نشد؛ فایل موفق ثبت نشد.', 503)
        expected = {'id': result['id'], 'size': size, 'key': result['key'], 'name': name, 'parent': parent}
        for attempt in range(3):
            cancelled()
            observed = self._json('GET', f'v2/flat/get-object/{result["id"]}/')
            mismatches = [key for key, value in expected.items() if observed.get(key) != value]
            if type(observed.get('size')) is not int and 'size' not in mismatches:
                mismatches.append('size')
            if not mismatches:
                return observed
            if attempt < 2:
                time.sleep(attempt + 1)
        raise BackupError('بررسی پس از ارسال تأیید نشد؛ فایل موفق ثبت نشد. فیلدهای ناسازگار: ' + ', '.join(mismatches), 503)

    def verify(self, object_id, expected_hash, expected_size, cancelled=lambda: None, metadata=None):
        for attempt in range(3):
            cancelled()
            try:
                return self._verify_once(object_id, expected_hash, expected_size, cancelled, metadata if attempt == 0 else None)
            except BackupError as error:
                if error.status not in (429, 502, 503, 504) or attempt == 2:
                    raise
                time.sleep(attempt + 1)

    def _verify_once(self, object_id, expected_hash, expected_size, cancelled, metadata=None):
        cancelled()
        result = metadata if (isinstance(metadata, dict) and metadata.get('id') == object_id
                              and isinstance(metadata.get('download_url'), str)) else self._json(
                                  'GET', f'v2/flat/get-object/{int(object_id)}/')
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
        return result


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
    def __init__(self, base, client=None, state_dir=None, port=8765):
        self.base = Path(base).resolve()
        self.state_dir = Path(state_dir).resolve() if state_dir else self.base / '.backup'
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config_path = self.state_dir / 'config.json'
        self.startup = StartupRegistration(self.state_dir, port)
        self.lock = threading.RLock()
        self.work_lock = threading.Lock()
        self.stop = threading.Event()
        self.shutdown = threading.Event()
        self.browse_lock = threading.Lock()
        self.quota_checked = None
        self.quota_next = 0
        self.quota_thread = None
        self.change_due = None
        self.pending_since = None
        self.change_generation = 0
        self.synced_generation = 0
        self.pending_paths = {}
        self.live_folder_stats = {}
        self.volume_saved = {}
        self.full_sync_generation = 0
        self.retry_at = 0
        self.retry_attempt = 0
        self.change_monitor = ChangeMonitor(self._on_source_change)
        self.client = client or HamrahiClient(self.state_dir)
        self.state = {'busy': False, 'action': None, 'phase': 'آماده', 'current': '',
                      'uploaded': 0, 'skipped': 0, 'bytes': 0, 'errors': [], 'lastResult': None,
                      'connection': None, 'quotaRefreshing': False, 'quotaError': None,
                      'lastTest': None, 'nextRun': None, 'pendingSync': False,
                      'watching': 0, 'watchBackend': None, 'watchError': None, 'lastChange': None,
                      'nextSync': None, 'trigger': None, 'runId': None, 'steps': [], 'fileStage': None}
        self.config_error = None
        self.config = {'version': 1, 'remoteRoot': cloud_name(socket.gethostname() or 'Computer'), 'intervalMinutes': 60,
                       'syncBehavior': 'mirror',
                       'automatic': False, 'syncOnChange': True, 'automaticPaused': False,
                       'debounceSeconds': 5, 'maxFileMiB': 0, 'settleSeconds': 15, 'uploadWorkers': 8,
                       'verifyUploads': True, 'startupEnabled': False, 'exclude': DEFAULT_EXCLUDES[:], 'folders': []}
        try:
            if self.config_path.exists():
                self.config = self.validate_config(json.loads(self.config_path.read_text('utf-8')))
        except Exception:
            self.config_error = 'تنظیمات ذخیره‌شده معتبر نیست؛ اجرای خودکار متوقف است.'
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS files (job TEXT, rel TEXT, sha TEXT, size INTEGER, mtime INTEGER, remote_id INTEGER, remote_name TEXT, PRIMARY KEY(job,rel))')
            db.execute('CREATE TABLE IF NOT EXISTS run_items (run TEXT, path TEXT, status TEXT, size INTEGER, error TEXT, remote_id INTEGER, PRIMARY KEY(run,path))')
            db.execute('CREATE TABLE IF NOT EXISTS folder_audits (job TEXT PRIMARY KEY, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS folder_volumes (job TEXT PRIMARY KEY, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS audit_files (job TEXT, rel TEXT, local_size INTEGER, cloud_size INTEGER, remote_id INTEGER, status TEXT, error TEXT, PRIMARY KEY(job,rel))')
            db.execute('CREATE TABLE IF NOT EXISTS verified_objects (remote_id INTEGER PRIMARY KEY, sha TEXT, size INTEGER, revision TEXT, verified_at REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS source_fingerprints (job TEXT, rel TEXT, size INTEGER, mtime INTEGER, ctime INTEGER, inode TEXT, sqlite_file INTEGER, PRIMARY KEY(job,rel))')
            db.execute('CREATE TABLE IF NOT EXISTS folder_schedules (job TEXT PRIMARY KEY, daily_time TEXT, next_run REAL)')
        self.configure_folder_schedules()
        try:
            self.history = json.loads((self.state_dir / 'history.json').read_text('utf-8'))[:30]
            if self.history:
                self.state['lastResult'] = self.history[0]
        except (OSError, ValueError):
            self.history = []
        self.recover_interrupted_run()
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
        result = {'version': 1, 'remoteRoot': cloud_name(str(data.get('remoteRoot', socket.gethostname() or 'Computer')))}
        result['syncBehavior'] = data.get('syncBehavior', 'mirror')
        if result['syncBehavior'] not in ('mirror', 'versioned'):
            raise BackupError('حالت سینک معتبر نیست.')
        if result['remoteRoot'] == 'Abr-Hamrahi-API' and not data.get('folders'):
            result['remoteRoot'] = cloud_name(socket.gethostname() or 'Computer')
        for key, default, low, high in [('intervalMinutes', 60, 5, 10080), ('maxFileMiB', 0, 0, 20480),
                                      ('uploadWorkers', 8, 1, 16),
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
        result['startupEnabled'] = data.get('startupEnabled', False) is True
        excludes = data.get('exclude', DEFAULT_EXCLUDES)
        if not isinstance(excludes, list) or len(excludes) > 200 or any(not isinstance(x, str) or len(x) > 250 for x in excludes):
            raise BackupError('فهرست استثناها معتبر نیست.')
        result['exclude'] = [x.strip() for x in excludes if x.strip()]
        folders = data.get('folders', [])
        if not isinstance(folders, list) or len(folders) > 100:
            raise BackupError('حداکثر صد پوشه می‌توانید اضافه کنید.')
        result['folders'], seen, ids, names = [], [], set(), set()
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
            # Never rewrite the source folder's name or append an internal ID.
            name = resolved.name
            if name.casefold() == METADATA.casefold():
                raise BackupError('این نام برای تاریخچهٔ داخلی برنامه رزرو شده است: ' + METADATA)
            if not name or name.casefold() in names:
                raise BackupError('نام پوشه‌های انتخابی باید متمایز باشد؛ دو پوشهٔ هم‌نام در مقصد ادغام نمی‌شوند.')
            names.add(name.casefold())
            sync_mode = item.get('syncMode', 'instant')
            daily_time = item.get('dailyTime', '02:00')
            if sync_mode not in ('instant', 'daily'):
                raise BackupError('حالت همگام‌سازی پوشه باید آنی یا روزانه باشد.')
            if not isinstance(daily_time, str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', daily_time):
                raise BackupError('ساعت همگام‌سازی روزانه معتبر نیست؛ نمونه: 02:00')
            result['folders'].append({'id': job_id, 'path': str(resolved),
                                      'name': name,
                                      'syncMode': sync_mode, 'dailyTime': daily_time,
                                      'enabled': item.get('enabled', True) is True})
        return result

    def save_config(self, data):
        if not self.work_lock.acquire(False):
            raise BackupError('تا پایان کار جاری صبر کنید.', 409)
        try:
            return self._save_config(data)
        finally:
            self.work_lock.release()

    def save_folder_schedule(self, data):
        with self.lock:
            job_id = data.get('job')
            sync_mode, daily_time = data.get('syncMode'), data.get('dailyTime')
            if sync_mode not in ('instant', 'daily') or not isinstance(daily_time, str) or not re.fullmatch(
                    r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', daily_time):
                raise BackupError('زمان‌بندی پوشه معتبر نیست.')
            folders = [dict(job) for job in self.config['folders']]
            selected = next((job for job in folders if job['id'] == job_id), None)
            if selected is None:
                raise BackupError('پوشه پیدا نشد.', 404)
            selected.update(syncMode=sync_mode, dailyTime=daily_time)
            config = self.config | {'folders': folders}
            atomic_json(self.config_path, config)
            self.config = config
            self.configure_folder_schedules()
            if self.config['automatic']:
                self._queue_sync()
            self._refresh_automation()
        self.configure_change_monitor()
        return {'saved': True, 'message': 'زمان‌بندی ذخیره شد.'}

    def _save_config(self, data):
        with self.lock:
            if self.state['busy']:
                raise BackupError('تا پایان کار جاری صبر کنید.', 409)
            result = self.validate_config(data)
            startup_changed = result['startupEnabled'] != self.config.get('startupEnabled', False)
            previous_startup = self.startup.read() if startup_changed else None
            if startup_changed:
                try:
                    self.startup.configure(result['startupEnabled'])
                except StartupPathError:
                    raise BackupError('فرمان استارتاپ از محدودیت ۲۶۰ کاراکتری ویندوز بیشتر است؛ مسیر نصب یا پوشهٔ تنظیمات کوتاه‌تری انتخاب کنید. تنظیمات ذخیره نشد.') from None
                except OSError:
                    raise BackupError('ثبت اجرای خودکار ویندوز ناموفق بود؛ تنظیمات ذخیره نشد.') from None
            try:
                atomic_json(self.config_path, result)
            except Exception:
                if startup_changed:
                    self.startup.write(previous_startup)
                raise
            self.config = result
            self.configure_folder_schedules()
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
            self.state['folderSchedules'] = {
                job['id']: {'mode': job.get('syncMode', 'instant'),
                            'dailyTime': job.get('dailyTime', '02:00'),
                            'nextRun': self.folder_schedules.get(job['id']) if job['enabled'] else None}
                for job in self.config['folders']}
            self.state['quotaStale'] = (self.quota_checked is None or
                                       time.monotonic() - self.quota_checked > 90 or
                                       bool(self.state['quotaError']))
            run = self.state.get('runId') if self.state['busy'] else (self.state.get('lastResult') or {}).get('id')
            counts, total_bytes, matched_bytes = {}, 0, 0
            if run:
                with self.db() as db:
                    counts = dict(db.execute('SELECT status,count(*) FROM run_items WHERE run=? GROUP BY status', (run,)))
                    total_bytes, matched_bytes = db.execute(
                        "SELECT coalesce(sum(size),0),coalesce(sum(CASE WHEN status IN ('ok','unchanged','preview','trashed') THEN size ELSE 0 END),0) FROM run_items WHERE run=?", (run,)).fetchone()
            total = sum(counts.values())
            done = sum(counts.get(key, 0) for key in ('ok', 'unchanged', 'preview', 'trashed', 'error'))
            successful = sum(counts.get(key, 0) for key in ('ok', 'unchanged', 'preview', 'trashed'))
            self.state['progress'] = {'total': total, 'done': done, 'failed': counts.get('error', 0),
                                      'successful': successful,
                                      'waiting': counts.get('deferred', 0),
                                      'totalBytes': total_bytes, 'matchedBytes': matched_bytes,
                                      'remainingBytes': max(0, total_bytes - matched_bytes),
                                      'percent': min(99.9, int(1000 * matched_bytes / total_bytes) / 10) if total_bytes and successful < total else 100 if total and successful == total else None}
            self.state['recovery'] = {'attempt': self.retry_attempt, 'retryAt': self.retry_at or None,
                                      'enabled': self.config['automatic'] and not self.config['automaticPaused']}
            from .audit import folder_summaries, match_summary
            folder_stats = folder_summaries(self)
            return json.loads(json.dumps({'config': self.config, 'state': self.state, 'history': self.history,
                                          'folderStats': folder_stats, 'syncMatch': match_summary(self, folder_stats),
                                          'authenticated': bool(self.client.tokens),
                                          'error': self.config_error or self.client.session_error,
                                          'startup': self.startup.status(),
                                          'provider': {'experimental': False, 'release': __version__, 'checkedOn': '2026-11-07',
                                                       'mode': self.config['syncBehavior']}}))

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def set_connection(self, connection):
        with self.lock:
            self.state.update(connection=connection, quotaError=None)
            self.quota_checked = time.monotonic()
            self.quota_next = self.quota_checked + (30 if self.state['busy'] else 60)

    def publish_volume(self, job, summary, force=False):
        with self.lock:
            self.live_folder_stats[job] = dict(summary)
            instant = time.monotonic()
            if force or instant - self.volume_saved.get(job, float('-inf')) >= 1:
                with self.db() as database:
                    database.execute('INSERT OR REPLACE INTO folder_volumes VALUES (?,?)',
                                     (job, json.dumps(summary)))
                self.volume_saved[job] = instant

    def refresh_quota(self, force=False):
        with self.lock:
            if self.shutdown.is_set() or not self.client.tokens:
                return {'started': False}
            if self.state['quotaRefreshing'] or (not force and time.monotonic() < self.quota_next):
                return {'started': False}
            tokens = self.client.tokens
            self.state['quotaRefreshing'] = True

            def worker():
                delay = 30
                try:
                    connection = self.client.quota(refresh=False)
                    with self.lock:
                        if not self.shutdown.is_set() and self.client.tokens is tokens:
                            self.set_connection(connection)
                except Exception as error:
                    delay = 300 if isinstance(error, BackupError) and error.status == 429 else 60
                    with self.lock:
                        if not self.shutdown.is_set() and self.client.tokens is tokens:
                            self.state['quotaError'] = 'دریافت ظرفیت تازه ناموفق بود؛ عدد آخرین بررسی نمایش داده می‌شود.'
                finally:
                    with self.lock:
                        self.state['quotaRefreshing'] = False
                        self.quota_next = time.monotonic() + (max(60, delay) if not self.state['busy'] else delay)

            self.quota_thread = threading.Thread(target=worker, daemon=True, name='cloud-quota')
            self.quota_thread.start()
        return {'started': True}

    def step(self, key, status='running'):
        with self.lock:
            for item in self.state['steps']:
                if item['key'] == key:
                    item['status'] = status

    def record_item(self, run, path, status, size=0, error='', remote_id=None):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO run_items VALUES (?,?,?,?,?,?)',
                       (run, str(path), status, size, error, remote_id))

    @staticmethod
    def file_size(path):
        try:
            return path.stat().st_size if path.is_file() else 0
        except OSError:
            return 0

    @staticmethod
    def fingerprint(path):
        info = path.stat()
        return info.st_size, info.st_mtime_ns, info.st_ctime_ns, str(info.st_ino)

    def items(self, data):
        run = data.get('run')
        offset = data.get('offset', 0)
        if not isinstance(run, str) or not re.fullmatch('[a-f0-9]{32}', run):
            raise BackupError('شناسهٔ گزارش معتبر نیست.')
        if type(offset) is not int or not 0 <= offset <= 100000000:
            raise BackupError('شمارهٔ صفحه معتبر نیست.')
        failed_only = data.get('failedOnly', False) is True
        where = 'run=?' + (" AND status IN ('error','deferred')" if failed_only else '')
        with self.db() as db:
            total = db.execute('SELECT count(*) FROM run_items WHERE ' + where, (run,)).fetchone()[0]
            rows = db.execute('SELECT path,status,size,error,remote_id FROM run_items WHERE ' + where +
                              " ORDER BY CASE status WHEN 'error' THEN 0 WHEN 'processing' THEN 1 WHEN 'deferred' THEN 2 WHEN 'queued' THEN 3 ELSE 4 END,path LIMIT 100 OFFSET ?",
                              (run, offset)).fetchall()
        return {'run': run, 'offset': offset, 'total': total,
                'items': [dict(zip(('path', 'status', 'bytes', 'error', 'remoteId'), row)) for row in rows]}

    def selected_item(self, data, require_error=False):
        # Local links and retry accept only a recorded file in a currently enabled source.
        run, raw = data.get('run'), data.get('path')
        if not isinstance(run, str) or not re.fullmatch('[a-f0-9]{32}', run) or not isinstance(raw, str):
            raise BackupError('فایل گزارش معتبر نیست.')
        with self.db() as db:
            row = db.execute('SELECT status FROM run_items WHERE run=? AND path=?', (run, raw)).fetchone()
        path = Path(raw)
        if not row:
            canonical_path = path.resolve()
            with self.db() as db:
                for job in self.config['folders']:
                    if not within(canonical_path, Path(job['path'])):
                        continue
                    summary = db.execute('SELECT payload FROM folder_audits WHERE job=?', (job['id'],)).fetchone()
                    if summary and json.loads(summary[0]).get('run') == run:
                        row = db.execute('SELECT status FROM audit_files WHERE job=? AND rel=?',
                                         (job['id'], canonical_path.relative_to(Path(job['path'])).as_posix())).fetchone()
                        break
        if not row or (require_error and row[0] not in ('error', 'deferred')):
            raise BackupError('فایل خطادار در این گزارش پیدا نشد.', 404)
        if (not path.is_absolute() or not (path.is_file() or path.is_dir()) or is_link(path)
                or any(is_link(parent) for parent in path.parents)
                or relevant_path(path, self.config, self.state_dir) is None):
            raise BackupError('فایل در پوشه‌های فعال و مجاز موجود نیست.', 403)
        return path.resolve()

    def finish_result(self, result):
        result['completedAt'] = now()
        result['ok'] = not result['errors']
        with self.lock:
            self.history = [h for h in self.history if h.get('id') != result['id']]
            self.history.insert(0, result)
            self.history = self.history[:30]
            atomic_json(self.state_dir / 'history.json', self.history)
            self.state.update(lastResult=result, errors=result['errors'])
            (self.state_dir / 'active-run.json').unlink(missing_ok=True)
        keep = [h['id'] for h in self.history if h.get('id')]
        if keep:
            with self.db() as db:
                db.execute('DELETE FROM run_items WHERE run NOT IN (' + ','.join('?' for _ in keep) + ')', keep)

    def recover_interrupted_run(self):
        draft = self.state_dir / 'active-run.json'
        if not draft.exists():
            return
        try:
            result = json.loads(draft.read_text('utf-8'))
            run = result['id']
            if not isinstance(run, str) or not re.fullmatch('[a-f0-9]{32}', run):
                raise ValueError()
            if any(h.get('id') == run for h in self.history):
                draft.unlink(missing_ok=True)
                return
            message = 'اجرای قبلی پیش از پایان قطع شد؛ فایل‌های باقی‌مانده نیاز به تلاش مجدد دارند.'
            with self.db() as db:
                db.execute("UPDATE run_items SET status='error',error=? WHERE run=? AND status IN ('queued','processing')", (message, run))
                rows = db.execute('SELECT status,size FROM run_items WHERE run=?', (run,)).fetchall()
            result.update(uploaded=sum(s in ('ok', 'preview') for s, _ in rows),
                          unchanged=sum(s == 'unchanged' for s, _ in rows),
                          files=sum(s != 'error' for s, _ in rows),
                          bytes=sum(n for s, n in rows if s in ('ok', 'preview')),
                          errors=[message])
            self.finish_result(result)
        except (OSError, ValueError, KeyError, TypeError):
            self.state['errors'] = ['گزارش اجرای قطع‌شده قابل بازیابی نیست؛ دوباره پیش‌نمایش را اجرا کنید.']

    def _on_source_change(self, path):
        with self.lock:
            if not self.config.get('automatic') or not self.config.get('syncOnChange'):
                return
            if not path:
                return
            candidates = [Path(path)]
            if candidates[0].name.endswith(('-wal', '-journal')):
                candidates.append(candidates[0].with_name(candidates[0].name.rsplit('-', 1)[0]))
            config = self.config | {'folders': self.instant_folders()}
            relevant = {resolved for candidate in candidates
                        if (resolved := relevant_path(candidate, config, self.state_dir)) is not None}
            for candidate in relevant:
                self._queue_sync(candidate if candidate.is_file() else None)
            if relevant:
                self.state['lastChange'] = now()

    def configure_change_monitor(self):
        with self.lock:
            config = json.loads(json.dumps(self.config))
            config['folders'] = self.instant_folders()
        status = self.change_monitor.configure(config, self.state_dir)
        with self.lock:
            if status['watching'] and status['watching'] != self.state['watching']:
                self._queue_sync()
            self.state.update(watching=status['watching'], watchBackend=status['backend'], watchError=status['error'])

    def _queue_sync(self, path=None):
        if self.change_generation <= self.synced_generation or self.pending_since is None:
            self.pending_since = time.time()
        self.change_generation += 1
        if path is None or len(self.pending_paths) >= 1000:
            self.full_sync_generation = self.change_generation
            self.pending_paths.clear()
        else:
            self.pending_paths[str(Path(path).resolve())] = self.change_generation
        delay = max(self.config['debounceSeconds'], self.config['settleSeconds'])
        self.change_due = min(time.time() + delay, self.pending_since + max(30, delay))

    def instant_folders(self):
        return [job for job in self.config['folders']
                if job['enabled'] and job.get('syncMode', 'instant') == 'instant']

    @staticmethod
    def next_daily_run(daily_time, after=None):
        current = datetime.fromtimestamp(time.time() if after is None else after)
        hour, minute = map(int, daily_time.split(':'))
        target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= current:
            target += timedelta(days=1)
        return target.timestamp()

    def configure_folder_schedules(self):
        with self.db() as db:
            previous = {job: (daily_time, next_run) for job, daily_time, next_run in
                        db.execute('SELECT job,daily_time,next_run FROM folder_schedules')}
            schedules = {}
            for job in self.config['folders']:
                if job.get('syncMode', 'instant') != 'daily':
                    continue
                daily_time = job.get('dailyTime', '02:00')
                saved = previous.get(job['id'])
                deadline = saved[1] if saved and saved[0] == daily_time else self.next_daily_run(daily_time)
                schedules[job['id']] = deadline
                db.execute('INSERT OR REPLACE INTO folder_schedules VALUES (?,?,?)',
                           (job['id'], daily_time, deadline))
            for job_id in previous.keys() - schedules.keys():
                db.execute('DELETE FROM folder_schedules WHERE job=?', (job_id,))
        self.folder_schedules = schedules

    def complete_daily_schedules(self, job_ids):
        with self.db() as db:
            for job in self.config['folders']:
                if job['id'] in job_ids and job.get('syncMode') == 'daily':
                    deadline = self.next_daily_run(job['dailyTime'])
                    db.execute('UPDATE folder_schedules SET next_run=? WHERE job=?', (deadline, job['id']))
                    self.folder_schedules[job['id']] = deadline

    def _refresh_automation(self):
        instant = self.instant_folders()
        pending = bool(instant) and self.change_generation > self.synced_generation
        self.state['pendingSync'] = pending
        deadlines = [deadline for deadline in (self.change_due if pending else None, self.state['nextRun'] if instant else None)
                     if deadline is not None]
        deadlines.extend(self.folder_schedules[job['id']] for job in self.config['folders']
                         if job['enabled'] and job.get('syncMode') == 'daily')
        ready = (self.config['automatic'] and not self.config['automaticPaused'] and
                 bool(self.client.tokens) and not self.config_error and not self.state['busy'])
        self.state['nextSync'] = max(min(deadlines), self.retry_at) if ready and deadlines else None

    def start(self, action, trigger='manual', only_paths=None, deep=False, only_jobs=None):
        if action not in ('preview', 'sync', 'audit', 'test', 'connection'):
            raise BackupError('عملیات معتبر نیست.')
        if not self.work_lock.acquire(False):
            raise BackupError('یک کار دیگر در حال اجراست.', 409)
        with self.lock:
            if self.shutdown.is_set() or (trigger != 'manual' and
                    (not self.config['automatic'] or self.config['automaticPaused'] or not self.client.tokens)):
                self.work_lock.release()
                raise BackupError('اجرای خودکار اکنون آماده نیست.', 409)
            generation = self.change_generation
            self.active_generation = generation
            covers_instant = only_jobs is None or {job['id'] for job in self.instant_folders()} <= only_jobs
            if trigger == 'change' and only_paths is None and self.full_sync_generation <= self.synced_generation:
                only_paths = set(self.pending_paths) or None
            self.stop.clear()
            self.state.update(busy=True, action=action, trigger=trigger, phase='شروع بررسی', current='',
                              uploaded=0, skipped=0, bytes=0, errors=[], runId=None, steps=[], fileStage=None)
            self.active_result = None
        def worker():
            succeeded = False
            try:
                if action == 'connection':
                    self.set_connection(self.client.connection())
                    self.update(phase='اتصال حساب تأیید شد')
                elif action == 'test':
                    self.test_roundtrip()
                elif action == 'audit':
                    from .audit import audit_folders
                    audit_folders(self, reuse=not deep)
                    succeeded = bool(self.state['lastResult'] and self.state['lastResult']['ok'])
                else:
                    self.sync(preview=action == 'preview', verify_existing=only_paths is None or trigger != 'change', only_paths=only_paths, only_jobs=only_jobs)
                    succeeded = bool(self.state['lastResult'] and self.state['lastResult']['ok'])
            except Exception as error:
                message = str(error) if isinstance(error, BackupError) else 'عملیات ناموفق بود؛ فایل موفق ثبت نشد. نوع خطا: ' + type(error).__name__
                self.update(phase='عملیات ناموفق', errors=[message])
                with self.lock:
                    for item in self.state['steps']:
                        if item['status'] == 'running':
                            item['status'] = 'error'
                if action == 'test':
                    self.update(lastTest={'at': now(), 'ok': False, 'message': message})
                if action in ('sync', 'preview', 'audit'):
                    with self.lock:
                        failed = self.active_result or {'id': uuid.uuid4().hex, 'at': now(), 'preview': action == 'preview', 'ok': False,
                                  'uploaded': self.state['uploaded'], 'unchanged': self.state['skipped'],
                                  'bytes': self.state['bytes'], 'errors': [message], 'samples': [],
                                  'excluded': 0, 'files': 0, 'manifests': []}
                        if message not in failed['errors']:
                            failed['errors'].append(message)
                        with self.db() as db:
                            transient = isinstance(error, BackupError) and error.status in (429, 502, 503, 504)
                            if transient:
                                db.execute("UPDATE run_items SET status='deferred',error=? WHERE run=? AND status='queued'",
                                           ('این فایل هنوز بررسی نشده؛ در انتظار ادامه پس از رفع خطای سرویس. علت: ' + message, failed['id']))
                            db.execute("UPDATE run_items SET status='error',error=? WHERE run=? AND status IN ('queued','processing')",
                                       (message, failed['id']))
                        self.finish_result(failed)
            finally:
                with self.lock:
                    if action in ('sync', 'audit'):
                        if covers_instant and (trigger != 'change' or self.state['nextRun'] is None):
                            self.state['nextRun'] = time.time() + self.config['intervalMinutes'] * 60 if self.config['automatic'] else None
                        if action == 'sync' and not only_paths and self.active_result:
                            self.complete_daily_schedules(set(self.active_result.get('completedJobs', [])))
                        if succeeded and covers_instant and (not only_paths or trigger == 'change'):
                            self.synced_generation = max(self.synced_generation, generation)
                            self.pending_paths = {path: value for path, value in self.pending_paths.items()
                                                  if value > generation}
                            if self.change_generation <= self.synced_generation:
                                self.retry_at = 0
                                self.retry_attempt = 0
                            if self.change_generation <= self.synced_generation:
                                self.change_due = None
                                self.pending_since = None
                        elif not succeeded and self.config['automatic']:
                            if covers_instant:
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

    def walk(self, root, config, errors, skips, on_error=None, only_paths=None, empty_directories=None):
        def failure(path, message):
            errors.append(message)
            if on_error:
                on_error(path, message)
        patterns = [x.replace('\\', '/').lower() for x in config['exclude']]
        def excluded(path):
            rel = path.relative_to(root).as_posix().lower()
            return (within(path, self.state_dir) or
                    any(fnmatch.fnmatchcase(rel, p) or fnmatch.fnmatchcase(path.name.lower(), p) for p in patterns))
        def visit(directory):
            self.cancelled()
            try:
                with os.scandir(directory) as stream:
                    entries = sorted(stream, key=lambda entry: entry.name.lower())
                if not entries and empty_directories is not None:
                    empty_directories.append(directory)
            except OSError:
                failure(directory, 'دسترسی به پوشه ممکن نیست: ' + str(directory))
                return
            for entry in entries:
                path = Path(entry.path)
                self.cancelled()
                try:
                    info = entry.stat(follow_symlinks=False)
                    if entry.is_symlink() or bool(getattr(info, 'st_file_attributes', 0) & 0x400):
                        failure(path, 'مسیر پیوندی برای جلوگیری از حلقه دنبال نشد: ' + str(path))
                    elif excluded(path):
                        skips.append(str(path))
                    elif entry.is_dir(follow_symlinks=False):
                        if only_paths is None or any(within(Path(p), path) for p in only_paths):
                            yield from visit(path)
                    elif entry.is_file(follow_symlinks=False):
                        if only_paths is not None and str(path.resolve()) not in only_paths:
                            continue
                        if config['maxFileMiB'] and info.st_size > config['maxFileMiB'] * 1048576:
                            failure(path, 'فایل از سقف اندازه بزرگ‌تر است: ' + str(path))
                        elif time.time() - info.st_mtime < config['settleSeconds']:
                            failure(path, 'فایل اخیراً تغییر کرده؛ در نوبت بعد دوباره بررسی می‌شود: ' + str(path))
                        else:
                            yield path
                except OSError:
                    failure(path, 'دسترسی به فایل ممکن نیست: ' + str(path))
        if not root.is_dir():
            failure(root, 'پوشه در دسترس نیست: ' + str(root))
            return
        if is_link(root):
            failure(root, 'پوشه به پیوند تبدیل شده است: ' + str(root))
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
        digest = hashlib.sha256()
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
                    digest.update(block)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise BackupError('فایل هنگام خواندن تغییر کرده است: ' + str(path))
        if any(is_link(p) for p in (path, *path.parents)):
            raise BackupError('مسیر فایل حین خواندن پیوندی شده است؛ ارسال متوقف شد.')
        if sqlite:
            with target.open('rb') as stream:
                while True:
                    self.cancelled()
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
        return digest.hexdigest(), target.stat().st_size, before.st_mtime_ns, sqlite

    def sync(self, preview=False, verify_existing=True, only_paths=None, only_jobs=None):
        config = json.loads(json.dumps(self.config))
        audit_generation = self.change_generation
        folders = [x for x in config['folders'] if x['enabled'] and (only_jobs is None or x['id'] in only_jobs)]
        if not folders:
            raise BackupError('حداقل یک پوشهٔ فعال اضافه کنید.')
        run = uuid.uuid4().hex
        result = {'id': run, 'at': now(), 'preview': preview, 'uploaded': 0, 'unchanged': 0, 'bytes': 0,
                  'files': 0, 'errors': [], 'excluded': 0, 'samples': [], 'manifests': [], 'completedJobs': [],
                  'retry': bool(only_paths) and self.state.get('trigger') != 'change',
                  'partial': bool(only_paths)}
        self.active_result = result
        self.audit_generation = None
        atomic_json(self.state_dir / 'active-run.json', result)
        self.update(runId=run, fileStage=None, steps=[
            {'key': 'connection', 'label': 'بررسی اتصال' if not preview else 'آماده‌سازی محلی', 'status': 'running'},
            {'key': 'scan', 'label': 'بررسی پوشه‌ها', 'status': 'pending'},
            {'key': 'transfer', 'label': 'پردازش فایل‌ها', 'status': 'pending'},
            {'key': 'manifest', 'label': 'ذخیرهٔ گزارش بازیابی' if not preview else 'ثبت پیش‌نمایش', 'status': 'pending'}])
        self.update(transfer={'sentBytes': 0, 'activeFiles': 0, 'bytesPerSecond': 0})
        transfer_lock = threading.Lock()
        transfer_started = time.monotonic()
        transfer_sent = {}
        transfer_total_sent = 0
        transfer_active = set()
        def upload_progress(path, sent=None, active=None):
            nonlocal transfer_total_sent
            with transfer_lock:
                if sent is not None:
                    before = transfer_sent.get(str(path), 0)
                    transfer_sent[str(path)] = max(before, sent)
                    transfer_total_sent += max(0, sent - before)
                if active is True:
                    transfer_active.add(str(path))
                elif active is False:
                    transfer_active.discard(str(path))
                total_sent = transfer_total_sent
                self.update(transfer={'sentBytes': total_sent, 'activeFiles': len(transfer_active),
                                      'bytesPerSecond': int(total_sent / max(1, time.monotonic() - transfer_started))})
        connection = None if preview else self.client.connection()
        if connection:
            self.set_connection(connection)
        remaining = max(0, connection['total'] - connection['used']) if connection else None
        self.step('connection', 'ok')
        self.step('scan')
        self.update(phase='شمارش فایل‌ها و ساخت صف ارسال', fileStage=None)
        jobs = []
        source_identities = {}
        scanned_files = 0
        for job in folders:
            root, skips, empty_directories = Path(job['path']), [], []
            scan_errors_before = len(result['errors'])
            try:
                identity = root.stat()
                source_identities[job['id']] = (identity.st_dev, identity.st_ino)
            except OSError:
                pass
            if only_paths and not any(within(Path(p), root) for p in only_paths):
                continue
            def scan_error(path, message):
                self.record_item(run, path, 'error', self.file_size(path), error=message)
                self.update(errors=list(result['errors']))
            paths, queued, sizes = [], [], {}
            for path in self.walk(root, config, result['errors'], skips, scan_error, only_paths, empty_directories):
                paths.append(path)
                sizes[path] = self.file_size(path)
                queued.append((run, str(path), 'queued', sizes[path], '', None))
                scanned_files += 1
                if len(queued) >= 256:
                    with self.db() as db:
                        db.executemany('INSERT OR REPLACE INTO run_items VALUES (?,?,?,?,?,?)', queued)
                    queued.clear()
                    self.update(phase=f'شمارش فایل‌ها: {scanned_files:,} فایل', current=job['path'])
            with self.db() as db:
                db.executemany('INSERT OR REPLACE INTO run_items VALUES (?,?,?,?,?,?)', queued)
            result['excluded'] += len(skips)
            paths.sort(key=lambda path: -sizes[path])
            jobs.append((job, paths, len(skips), empty_directories, len(result['errors']) > scan_errors_before))
        if only_paths and not any(paths for _, paths, _, _, _ in jobs) and not result['errors']:
            raise BackupError('فایل انتخابی پیش از تلاش مجدد حذف یا جابه‌جا شده است.')
        self.step('scan', 'error' if result['errors'] else 'ok')
        self.step('transfer')
        remote_cache = {}
        from .audit import verify_object
        layout = CloudLayout(self.client, config['remoteRoot'], BackupError, cloud_name, self.cancelled,
                             lambda item, digest, size: verify_object(self, item['id'], digest, size, item, reuse=True))
        remote_parent = layout.parent
        with self.db(), tempfile.TemporaryDirectory(prefix='staging-', dir=self.state_dir) as staging:
            staged = Path(staging) / 'file'
            manifests = []
            for job, paths, excluded_count, empty_directories, scan_failed in jobs:
                job_errors_before = len(result['errors'])
                if paths and not preview and not only_paths and hasattr(self.client, 'list_tree'):
                    self.update(phase='دریافت یکجای فهرست پوشهٔ ابری', current=job['path'])
                    layout.prime(layout.parent(job, Path('inventory')))
                live_summary = None
                if not preview and not only_paths:
                    from .audit import config_stamp, local_usage, volume_stamp
                    self.update(phase='اندازه‌گیری حجم کل پوشه', current=job['path'], fileStage=None)
                    cloud_items = []
                    pending_folders = [layout.parent(job, Path('inventory'))]
                    seen_folders = set()
                    while pending_folders:
                        self.cancelled()
                        folder_id = pending_folders.pop()
                        if folder_id in seen_folders:
                            raise BackupError('ساختار پوشهٔ ابری چرخه دارد.', 503)
                        seen_folders.add(folder_id)
                        for item in layout.listing(folder_id).values():
                            if item.get('key'):
                                cloud_items.append(item)
                            else:
                                pending_folders.append(item['id'])
                    live_summary = dict(local_usage(self, Path(job['path'])),
                        run=run, checkedAt=now(), measuredAt=time.time_ns(), configStamp=config_stamp(config),
                        volumeStamp=volume_stamp(config),
                        localBytes=sum(self.file_size(path) for path in paths), localFiles=len(paths),
                        cloudBytes=sum(item['size'] for item in cloud_items), cloudFiles=len(cloud_items),
                        excluded=excluded_count, complete=False, issues=0, verifiedFiles=0,
                        assurance='size-and-identity', inProgress=True)
                    self.publish_volume(job['id'], live_summary, force=True)
                def cloud_volume(delta, count):
                    if live_summary is not None:
                        with self.lock:
                            live_summary['cloudBytes'] += delta
                            live_summary['cloudFiles'] += count
                            live_summary['checkedAt'] = now()
                            live_summary['measuredAt'] = time.time_ns()
                            self.publish_volume(job['id'], live_summary)
                manifest = []
                errors = result['errors']
                root = Path(job['path'])
                with self.db() as db:
                    indexed = {row[0]: row[1:] for row in db.execute(
                        'SELECT rel,sha,remote_id,remote_name,size FROM files WHERE job=?', (job['id'],))}
                    fingerprints = {row[0]: row[1:] for row in db.execute(
                        'SELECT rel,size,mtime,ctime,inode,sqlite_file FROM source_fingerprints WHERE job=?', (job['id'],))}
                unchanged_records = []
                fingerprint_records = []
                def flush_unchanged():
                    with self.db() as db:
                        db.executemany('INSERT OR REPLACE INTO run_items VALUES (?,?,?,?,?,?)', unchanged_records)
                        db.executemany('INSERT OR REPLACE INTO source_fingerprints VALUES (?,?,?,?,?,?,?)', fingerprint_records)
                    unchanged_records.clear()
                    fingerprint_records.clear()
                preparation_lock, result_lock, snapshot_lock = threading.Lock(), threading.Lock(), threading.Lock()
                outage_lock, outage_failures = threading.Lock(), 0
                batch_failed = threading.Event()
                def process_path(path):
                    nonlocal remaining, outage_failures
                    if batch_failed.is_set():
                        return
                    staged = Path(staging) / uuid.uuid4().hex
                    relative = path.relative_to(root)
                    self.cancelled()
                    self.update(phase='ساخت نسخهٔ موقت و محاسبهٔ هش', current=str(path), fileStage='snapshot')
                    try:
                        source_before = self.fingerprint(path)
                        old = indexed.get(relative.as_posix())
                        previous = fingerprints.get(relative.as_posix())
                        fast = False
                        if (old and previous and not previous[4] and tuple(previous[:4]) == source_before
                                and not preview and not only_paths and not config['verifyUploads']
                                and self.state.get('trigger') in ('change', 'schedule')):
                            with preparation_lock:
                                parent = remote_parent(job, relative)
                                remote = layout.listing(parent).get(path.name)
                                fast = bool(remote and remote.get('key') and remote['id'] == old[1]
                                            and remote.get('size') == old[3] == source_before[0])
                        if fast:
                            digest, size, mtime, is_sqlite = old[0], old[3], source_before[1], False
                        else:
                            with snapshot_lock:
                                if batch_failed.is_set():
                                    return
                                digest, size, mtime, is_sqlite = self.snapshot(path, staged)
                        if config['maxFileMiB'] and size > config['maxFileMiB'] * 1048576:
                            raise BackupError('snapshot از سقف اندازه بزرگ‌تر است: ' + str(path))
                        filename = path.name
                        object_id, exists = None, False
                        replacement = None
                        if not preview:
                            with preparation_lock:
                                parent = remote_parent(job, relative)
                                if parent not in remote_cache:
                                    remote_cache[parent] = layout.listing(parent)
                                remote = layout.adopt_legacy(parent, filename, old, remote_cache[parent])
                                if remote and old and old[1] == remote['id'] and old[0] != digest:
                                    if remaining is not None and size > remaining:
                                        raise BackupError('ظرفیت باقی‌ماندهٔ حساب برای این فایل کافی نیست.', 413)
                                    if config['syncBehavior'] == 'mirror':
                                        replacement = remote
                                    else:
                                        self.update(phase='بررسی و انتقال نسخهٔ قبلی به تاریخچه', fileStage='verify')
                                        layout.archive(job, relative, remote, old)
                                        cloud_volume(-int(remote['size']), -1)
                                        remote_cache[parent].pop(filename, None)
                                    remote = None
                                if not remote and old and replacement is None:
                                    remote = layout.restore_version(job, relative, digest, size, parent)
                                    if remote:
                                        remote_cache[parent][filename] = remote
                                        cloud_volume(int(remote['size']), 1)
                                if remote:
                                    if int(remote.get('size', -1)) != size or not remote.get('key'):
                                        raise BackupError('نسخهٔ ابری هم‌نام با اندازهٔ ناسازگار وجود دارد: ' + str(relative))
                                    object_id, exists = remote['id'], True
                                    if (verify_existing and config['verifyUploads']) or not old or old[0] != digest or old[1] != object_id:
                                        self.update(phase='بررسی نسخهٔ موجود در ابر', fileStage='verify')
                                        from .audit import verify_object
                                        verify_object(self, object_id, digest, size, remote, reuse=True)
                                if not remote:
                                    if remaining is not None and size > remaining:
                                        raise BackupError("ظرفیت باقی‌ماندهٔ حساب کافی نیست.", 413)
                                    if remaining is not None:
                                        remaining -= size
                            if not remote:
                                self.cancelled()
                                self.update(phase='ارسال فایل', fileStage='upload')
                                self.record_item(run, path, 'processing', size)
                                upload_progress(path, active=True)
                                uploaded = self.client.upload(staged, filename, parent, cancelled=self.cancelled,
                                                              progress=lambda sent: upload_progress(path, sent), replace=replacement)
                                upload_progress(path, sent=size, active=False)
                                object_id = uploaded['id']
                                cloud_volume(size - int(replacement['size']) if replacement else size, 0 if replacement else 1)
                                if config['verifyUploads']:
                                    self.update(phase='دریافت مجدد و بررسی SHA-256', fileStage='verify')
                                    from .audit import verify_object
                                    verify_object(self, object_id, digest, size, uploaded)
                                with preparation_lock:
                                    remote_cache[parent][filename] = uploaded
                        else:
                            exists = bool(old and old[0] == digest)
                        with result_lock:
                            entry = {'path': relative.as_posix(), 'sha256': digest, 'size': size,
                                     'remoteId': object_id, 'remoteName': filename, 'sqliteSnapshot': is_sqlite}
                            if not preview:
                                self.update(fileStage='record', phase='ثبت نتیجهٔ فایل')
                                stable = source_before == self.fingerprint(path)
                                fingerprint = (job['id'], relative.as_posix(), *source_before, int(is_sqlite))
                                record = (run, str(path), 'unchanged' if exists else 'ok', size, '', object_id)
                                if exists and old and (digest, object_id, filename, size) == tuple(old):
                                    unchanged_records.append(record)
                                    if stable:
                                        fingerprint_records.append(fingerprint)
                                    if len(unchanged_records) >= 32:
                                        flush_unchanged()
                                else:
                                    with self.db() as db:
                                        db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)',
                                                   (job['id'], relative.as_posix(), digest, size, mtime, object_id, filename))
                                        db.execute('INSERT OR REPLACE INTO run_items VALUES (?,?,?,?,?,?)', record)
                                        if stable:
                                            db.execute('INSERT OR REPLACE INTO source_fingerprints VALUES (?,?,?,?,?,?,?)', fingerprint)
                            else:
                                self.record_item(run, path, 'unchanged' if exists else 'preview', size)
                            manifest.append(entry)
                            result['files'] += 1
                            if exists:
                                result['unchanged'] += 1
                            else:
                                result['uploaded'] += 1
                                result['bytes'] += size
                            if len(result['samples']) < 100:
                                result['samples'].append({'path': str(path), 'bytes': size, 'changed': not exists})
                            self.update(uploaded=result['uploaded'], skipped=result['unchanged'], bytes=result['bytes'])
                            with outage_lock:
                                outage_failures = 0
                    except (OSError, sqlite3.Error, BackupError) as error:
                        if self.stop.is_set():
                            raise
                        message = str(error) if isinstance(error, BackupError) else 'خواندن یا snapshot فایل ناموفق بود: ' + str(path)
                        self.record_item(run, path, 'error', self.file_size(path), error=message)
                        errors.append(message)
                        self.update(errors=list(errors))
                        if isinstance(error, BackupError):
                            with outage_lock:
                                if error.status in (502, 503, 504):
                                    outage_failures += 1
                                outage = outage_failures >= 8
                            if error.status in (401, 429) or outage:
                                batch_failed.set()
                                raise
                    finally:
                        upload_progress(path, active=False)
                        staged.unlink(missing_ok=True)
                try:
                    with ThreadPoolExecutor(max_workers=config['uploadWorkers'], thread_name_prefix="cloud-file") as executor:
                        remaining_paths, pending = iter(paths), set()
                        def submit_next():
                            candidate = next(remaining_paths, None)
                            if candidate is not None:
                                pending.add(executor.submit(process_path, candidate))
                        for worker_number in range(config['uploadWorkers']):
                            submit_next()
                        while pending:
                            completed, pending = wait(pending, return_when=FIRST_COMPLETED)
                            for future in completed:
                                future.result()
                                self.cancelled()
                                submit_next()
                finally:
                    flush_unchanged()
                    if live_summary is not None:
                        self.publish_volume(job['id'], live_summary, force=True)
                if not preview:
                    for directory in empty_directories:
                        self.cancelled()
                        try:
                            self.update(phase='همگام‌سازی پوشهٔ خالی', current=str(directory))
                            layout.parent(job, directory.relative_to(root) / '__directory__')
                        except BackupError as error:
                            result['errors'].append(str(error))
                            self.record_item(run, directory, 'error', error=str(error))
                if not preview and not only_paths:
                    if config['syncBehavior'] == 'mirror' and not scan_failed and len(result['errors']) == job_errors_before:
                        from .mirror import prune_cloud
                        prune_cloud(self, job, config, layout, run, result, source_identities.get(job['id']))
                    from .audit import capture_synced_folder
                    self.update(phase='اندازه‌گیری پوشهٔ محلی و ابری', current=job['path'], fileStage=None)
                    summary = capture_synced_folder(self, config, job, paths, result,
                                                    verify_existing and config['verifyUploads'])
                    summary['excluded'] = excluded_count
                    with self.db() as db:
                        db.execute('UPDATE folder_audits SET payload=? WHERE job=?',
                                   (json.dumps(summary), job['id']))
                    with self.lock:
                        self.live_folder_stats.pop(job['id'], None)
                manifests.append((job, manifest, not scan_failed and len(result['errors']) == job_errors_before))
            self.step('transfer', 'error' if result['errors'] else 'ok')
            self.step('manifest')
            for job, manifest, complete in manifests:
                if not preview:
                    if config['syncBehavior'] == 'mirror':
                        if complete:
                            result['completedJobs'].append(job['id'])
                        continue
                    self.cancelled()
                    self.update(phase='ذخیرهٔ فهرست بازیابی', current=job['path'], fileStage=None)
                    manifest_file = Path(staging) / 'manifest.json'
                    atomic_json(manifest_file, {'schema': 2,
                                                'folder': job['name'], 'files': sorted(manifest, key=lambda x: x['path']), 'errorCount': len(errors),
                                                'exclude': config['exclude'], 'mode': 'one-way-versioned',
                                                'partial': bool(only_paths)})
                    digest = hashlib.sha256(manifest_file.read_bytes()).hexdigest()
                    name = '_manifest-' + digest + '.json'
                    parent = layout.manifests(job)
                    obj = layout.listing(parent).get(name)
                    if not obj:
                        obj = self.client.upload(manifest_file, name, parent, cancelled=self.cancelled)
                    if not obj.get('key'):
                        raise BackupError('فهرست بازیابی مقصد با یک پوشه تعارض دارد.')
                    if config['verifyUploads']:
                        self.client.verify(obj['id'], hashlib.sha256(manifest_file.read_bytes()).hexdigest(), manifest_file.stat().st_size, cancelled=self.cancelled)
                    result['manifests'].append({'folder': job['name'], 'remoteId': obj['id'], 'name': name})
                    if complete:
                        result['completedJobs'].append(job['id'])
        if not preview and not only_paths and only_jobs is None:
            self.audit_generation = audit_generation
        self.finish_result(result)
        self.step('manifest', 'ok')
        self.update(phase=('پیش‌نمایش آماده است' if preview else 'پشتیبان‌گیری پایان یافت') + ('؛ نیاز به بررسی خطاها' if result['errors'] else ''), fileStage=None)

    def test_roundtrip(self):
        self.update(phase='بررسی اتصال حساب')
        self.set_connection(self.client.connection())
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
                try:
                    self.configure_change_monitor()
                    self.refresh_quota()
                    self.scheduler_tick()
                except Exception:
                    # A transient watcher/OS failure must not silently kill
                    # automation. Keep private exception text out of the UI.
                    self.update(watchError='پایش خودکار موقتاً ناموفق بود؛ تلاش دوباره انجام می‌شود.')
                    self.shutdown.wait(5)
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
            current = time.time()
            instant = self.instant_folders()
            interval_due = bool(instant) and self.state['nextRun'] is not None and self.state['nextRun'] <= current
            change_due = bool(instant) and self.change_generation > self.synced_generation and self.change_due is not None and self.change_due <= current
            daily_jobs = {job['id'] for job in self.config['folders'] if job['enabled'] and
                          job.get('syncMode') == 'daily' and self.folder_schedules[job['id']] <= current}
            only_jobs = daily_jobs | ({job['id'] for job in instant} if interval_due or change_due else set())
            trigger = 'schedule' if interval_due else 'daily' if daily_jobs else 'change'
        try:
            if only_jobs:
                self.start('sync', trigger=trigger, only_jobs=only_jobs)
        except BackupError:
            pass

    def handle(self, action, data):
        if action == 'folder-schedule':
            return self.save_folder_schedule(data)
        if action == 'connection':
            if not self.client.tokens:
                raise BackupError('ابتدا در حساب ابرهمراهی وارد شوید.', 401)
            return self.refresh_quota(force=True)
        if action == 'audit':
            if type(data.get('deep', False)) is not bool:
                raise BackupError('نوع بررسی محتوا معتبر نیست.')
            return self.start('audit', deep=data.get('deep', False))
        if action == 'repair-audit':
            from .audit import repair_paths
            return self.start('sync', only_paths=repair_paths(self))
        if action == 'folder-files':
            from .audit import folder_files
            return folder_files(self, data)
        if action == 'items':
            return self.items(data)
        if action == 'retry':
            path = self.selected_item(data, require_error=True)
            return self.start('sync', only_paths=None if path.is_dir() else {str(path)})
        if action == 'reveal':
            path = self.selected_item(data)
            if os.name != 'nt':
                raise BackupError('نمایش فایل در این سیستم پشتیبانی نمی‌شود.')
            # Select in Explorer rather than execute arbitrary scripts or shortcuts.
            subprocess.Popen([str(Path(os.environ.get('WINDIR', r'C:\Windows')) / 'explorer.exe'),
                              '/select,', str(path)])
            return {'message': 'محل فایل باز شد.'}
        if action == 'config':
            return self.save_config(data)
        if action in ('preview', 'sync', 'audit', 'test', 'connection'):
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
                        self.state['quotaError'] = None
                        self.quota_checked = None
                        self.quota_next = 0
                        (self.state_dir / 'last-test.json').unlink(missing_ok=True)
                    self.configure_change_monitor()
                    return {'message': 'نشست محلی پاک شد؛ اجرای خودکار خاموش است.'}
                result = self.client.auth(action, data)
                if action == 'login':
                    with self.lock:
                        self.state.update(connection=None, lastTest=None, quotaError=None)
                        self.quota_checked = None
                        self.quota_next = 0
                    (self.state_dir / 'last-test.json').unlink(missing_ok=True)
                return result
            finally:
                self.work_lock.release()
        raise BackupError('مسیر یافت نشد.', 404)

    def browse(self):
        with self.lock:
            if self.shutdown.is_set():
                raise BackupError('برنامه در حال بسته‌شدن است.', 409)
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
        frozen = getattr(sys, 'frozen', False)
        if not frozen and not script.is_file():
            raise BackupError('فایل انتخاب‌گر پوشه در نصب برنامه پیدا نشد.')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        executable = Path(sys.executable)
        if executable.name.lower() == 'pythonw.exe':
            executable = executable.with_name('python.exe')
        try:
            with tempfile.TemporaryDirectory(prefix='picker-', dir=self.state_dir) as picker_dir:
                output = Path(picker_dir) / 'result.json'
                command = ([str(executable), '--folder-picker', str(output)] if frozen
                           else [str(executable), '-I', str(script)])
                result = subprocess.run(command, capture_output=True, timeout=180, creationflags=flags)
                raw = output.read_bytes() if frozen and not result.returncode else result.stdout
            if result.returncode:
                raise BackupError('پنجرهٔ انتخاب پوشه باز نشد؛ مسیر را دستی وارد کنید یا نصب برنامه را ترمیم کنید.')
            payload = json.loads(raw.decode('utf-8-sig'))
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
