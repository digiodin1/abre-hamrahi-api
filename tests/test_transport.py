import io
import http.client
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from hamrahi_backup.transport import PooledHTTPSOpener


class WireResponse:
    def __init__(self, body=b'hello', status=200, will_close=False):
        self.body = io.BytesIO(body)
        self.length = len(body)
        self.status = status
        self.reason = 'synthetic'
        self.will_close = will_close
        self.headers = {'Content-Length': str(self.length)}
        self.closed = False

    def read(self, amount=None):
        result = self.body.read(-1 if amount is None else amount)
        self.length -= len(result)
        return result

    def isclosed(self):
        return self.length == 0

    def close(self):
        self.closed = True


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.no_proxy = patch('urllib.request.getproxies', return_value={})
        self.no_proxy.start()
        self.factory = patch('http.client.HTTPSConnection')
        self.connection_type = self.factory.start()
        self.connection = self.connection_type.return_value
        self.connection.sock = None
        self.connection.getresponse.side_effect = lambda: WireResponse()
        self.fallback = Mock()
        self.opener = PooledHTTPSOpener(self.fallback)

    def tearDown(self):
        self.opener.close()
        self.factory.stop()
        self.no_proxy.stop()

    def request(self, url='https://storage.example/file', method='GET'):
        return urllib.request.Request(url, method=method)

    def test_consumed_responses_reuse_tls_connection(self):
        for number in range(3):
            with self.opener.open(self.request()) as response:
                self.assertEqual(response.read(), b'hello')
        self.assertEqual(self.connection_type.call_count, 1)
        self.assertEqual(self.connection.request.call_count, 3)

    def test_partial_body_is_closed_and_never_reused(self):
        with self.opener.open(self.request()) as response:
            self.assertEqual(response.read(1), b'h')
        with self.opener.open(self.request()) as response:
            response.read()
        self.assertEqual(self.connection_type.call_count, 2)
        self.connection.close.assert_called()

    def test_server_connection_close_is_honored(self):
        self.connection.getresponse.side_effect = lambda: WireResponse(will_close=True)
        for number in range(2):
            with self.opener.open(self.request()) as response:
                response.read()
        self.assertEqual(self.connection_type.call_count, 2)

    def test_origins_have_separate_connections(self):
        for host in ('api.example', 'storage.example'):
            with self.opener.open(self.request('https://' + host + '/file')) as response:
                response.read()
        self.assertEqual(self.connection_type.call_count, 2)

    def test_ambiguous_post_is_never_replayed(self):
        self.connection.request.side_effect = ConnectionResetError()
        with self.assertRaises(urllib.error.URLError):
            self.opener.open(self.request(method='POST'))
        self.assertEqual(self.connection.request.call_count, 1)
        self.connection.close.assert_called_once()

    def test_redirect_is_returned_as_error_without_following(self):
        self.connection.getresponse.side_effect = lambda: WireResponse(status=302)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.opener.open(self.request(method='POST'))
        self.assertEqual(caught.exception.read(), b'hello')
        caught.exception.close()
        self.assertEqual(self.connection.request.call_count, 1)

    def test_proxy_uses_existing_proxy_handler(self):
        with patch('urllib.request.getproxies', return_value={'https': 'http://proxy.example:8080'}), \
                patch('urllib.request.proxy_bypass', return_value=False):
            request = self.request()
            self.assertIs(self.opener.open(request), self.fallback.open.return_value)
        self.connection_type.assert_not_called()

    def test_interrupted_response_is_not_reused(self):
        response = WireResponse()
        response.read = Mock(side_effect=http.client.IncompleteRead(b'partial'))
        self.connection.getresponse.side_effect = lambda: response
        with self.assertRaises(urllib.error.URLError):
            with self.opener.open(self.request()) as result:
                result.read()
        self.connection.close.assert_called_once()
        self.assertEqual(self.opener.idle, {})

    def test_non_https_and_credentials_are_rejected(self):
        for url in ('http://api.example/', 'https://user:password@api.example/'):
            with self.assertRaises(urllib.error.URLError):
                self.opener.open(self.request(url))
        self.connection_type.assert_not_called()

    def test_shutdown_releases_idle_connections(self):
        with self.opener.open(self.request()) as response:
            response.read()
        self.opener.close()
        self.connection.close.assert_called_once()
        with self.assertRaises(urllib.error.URLError):
            self.opener.open(self.request())
