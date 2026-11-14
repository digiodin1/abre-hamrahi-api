"""Bounded HTTPS keep-alive transport with the system proxy fallback."""
import http.client
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class PooledResponse:
    def __init__(self, owner, origin, connection, response):
        self.owner, self.origin = owner, origin
        self.connection, self.response = connection, response
        self.headers, self.status = response.headers, response.status
        self.complete = response.length == 0
        self.closed = False

    def read(self, amount=None):
        try:
            data = self.response.read(amount)
        except (OSError, http.client.HTTPException) as error:
            self.complete = False
            self.close()
            raise urllib.error.URLError(type(error).__name__) from None
        if amount is None or self.response.isclosed():
            self.complete = True
        return data

    def close(self):
        if self.closed:
            return
        self.closed = True
        reusable = self.complete and not self.response.will_close
        self.response.close()
        self.owner.release(self.origin, self.connection, reusable)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class PooledHTTPSOpener:
    def __init__(self, fallback, max_idle=16):
        self.fallback = fallback
        self.max_idle = max_idle
        self.context = ssl.create_default_context()
        self.lock = threading.Lock()
        self.idle = {}
        self.closed = False

    def open(self, request, timeout=60):
        address = urllib.parse.urlsplit(request.full_url)
        if address.scheme != 'https' or address.username or address.password or not address.hostname:
            raise urllib.error.URLError('HTTPS URL required')
        if (urllib.request.getproxies().get('https') and
                not urllib.request.proxy_bypass(address.hostname)):
            return self.fallback.open(request, timeout=timeout)
        origin = (address.hostname, address.port or 443)
        connection = None
        with self.lock:
            if self.closed:
                raise urllib.error.URLError('Transport closed')
            bucket = self.idle.get(origin, [])
            while bucket:
                candidate, stored = bucket.pop()
                if time.monotonic() - stored < 20:
                    connection = candidate
                    break
                candidate.close()
        if connection is None:
            connection = http.client.HTTPSConnection(*origin, timeout=timeout, context=self.context)
        try:
            connection.timeout = timeout
            if connection.sock is not None:
                connection.sock.settimeout(timeout)
            target = urllib.parse.urlunsplit(('', '', address.path or '/', address.query, ''))
            connection.request(request.get_method(), target, body=request.data,
                               headers=dict(request.header_items()))
            response = PooledResponse(self, origin, connection, connection.getresponse())
        except (OSError, http.client.HTTPException) as error:
            connection.close()
            raise urllib.error.URLError(type(error).__name__) from None
        if response.status >= 300:
            raise urllib.error.HTTPError(request.full_url, response.status,
                                         response.response.reason, response.headers, response)
        return response

    def release(self, origin, connection, reusable):
        with self.lock:
            if reusable and not self.closed and sum(map(len, self.idle.values())) < self.max_idle:
                self.idle.setdefault(origin, []).append((connection, time.monotonic()))
                return
        connection.close()

    def close(self):
        with self.lock:
            self.closed = True
            connections = [connection for bucket in self.idle.values() for connection, _ in bucket]
            self.idle.clear()
        for connection in connections:
            connection.close()
