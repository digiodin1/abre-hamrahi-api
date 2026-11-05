"""Loopback-only Abr Hamrahi API panel with no filesystem routes or request logs."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import ipaddress
import json
import secrets
import threading

from .engine import BackupError

MAX_BODY = 64 * 1024
ASSETS = {
    '/': ('backup.html', 'text/html; charset=utf-8'),
    '/backup': ('backup.html', 'text/html; charset=utf-8'),
    '/backup.js': ('backup.js', 'text/javascript; charset=utf-8'),
    '/backup.css': ('backup.css', 'text/css; charset=utf-8'),
}
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"


class PanelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, manager, port=8765):
        self.manager = manager
        self.csrf = secrets.token_urlsafe(32)
        self.slots = threading.BoundedSemaphore(16)
        self.assets = {path: (files('hamrahi_backup').joinpath('web', name).read_bytes(), mime)
                       for path, (name, mime) in ASSETS.items()}
        super().__init__(('127.0.0.1', port), Handler)
        self.hosts = {f'127.0.0.1:{self.server_port}', f'localhost:{self.server_port}'}
        self.origins = {'http://' + host for host in self.hosts}

    def process_request(self, request, address):
        if not self.slots.acquire(False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # Do not log paths, request bodies, cookies or credentials on HTTP errors.
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = 'AbrHamrahiAPI'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.reply(code, {'error': 'درخواست معتبر نیست.'})

    def reply(self, status, value, content_type='application/json; charset=utf-8'):
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)

    def allowed(self, require_origin=False):
        try:
            loopback = ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            loopback = False
        return (loopback
                and len(self.headers.get_all('Host', [])) == 1
                and self.headers.get('Host') in self.server.hosts
                and (self.headers.get('Origin') in self.server.origins if require_origin
                     else self.headers.get('Origin', next(iter(self.server.origins))) in self.server.origins)
                and self.headers.get('Sec-Fetch-Site', 'none') in ('none', 'same-origin'))

    def do_GET(self):
        if not self.allowed():
            self.reply(403, {'error': 'دسترسی فقط از پنل محلی مجاز است.'})
        elif self.path == '/api/backup':
            self.reply(200, self.server.manager.public() | {'csrfToken': self.server.csrf})
        elif self.path in self.server.assets:
            body, mime = self.server.assets[self.path]
            self.reply(200, body, mime)
        else:
            self.reply(404, {'error': 'مسیر یافت نشد.'})

    def do_POST(self):
        token = self.headers.get('X-Abr-Hamrahi-CSRF', '')
        if not self.allowed(require_origin=True) or not hmac.compare_digest(token, self.server.csrf):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if 0 <= length <= MAX_BODY:
                    self.rfile.read(length)
            except (ValueError, OSError, TimeoutError):
                pass
            self.reply(403, {'error': 'درخواست از پنل محلی تأیید نشد؛ صفحه را تازه کنید.'})
            return
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
            self.reply(400, {'error': 'طول درخواست معتبر نیست.'})
            return
        try:
            length = int(self.headers.get('Content-Length', '-1'))
            if not 0 <= length <= MAX_BODY:
                raise BackupError('درخواست بیش از حد مجاز بزرگ است.', 413)
            if self.headers.get_content_type() != 'application/json':
                raise BackupError('نوع درخواست باید JSON باشد.', 415)
            body = self.rfile.read(length)
            if len(body) != length:
                raise BackupError('درخواست ناقص است.')
            data = json.loads(body)
            if not isinstance(data, dict):
                raise BackupError('درخواست باید یک شیء JSON باشد.')
            prefix = '/api/backup/'
            if not self.path.startswith(prefix):
                raise BackupError('مسیر یافت نشد.', 404)
            action = self.path[len(prefix):]
            result = self.server.manager.browse() if action == 'browse' else self.server.manager.handle(action, data)
            self.reply(200, result)
        except BackupError as error:
            self.reply(error.status, {'error': str(error), 'captcha': error.captcha})
        except (ValueError, UnicodeError):
            self.reply(400, {'error': 'ساختار JSON معتبر نیست.'})
        except TimeoutError:
            self.reply(408, {'error': 'مهلت درخواست تمام شد.'})
        except Exception:
            self.reply(500, {'error': 'عملیات محلی ناموفق بود؛ تنظیمات و فضای دیسک را بررسی کنید.'})
