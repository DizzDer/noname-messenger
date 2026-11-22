"""Shared API client; tokens are held in memory and TLS verification stays on."""
import ipaddress
import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPSHandler, ProxyHandler, Request, HTTPRedirectHandler


class ClientError(Exception):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class Client:
    def __init__(self, url='http://127.0.0.1:8765', ca=None, ca_pem=None):
        try:
            if not isinstance(url, str):
                raise ValueError()
            url = url.strip()
            parsed = urlsplit(url)
            port = parsed.port
            if port is not None and port < 1:
                raise ValueError()
        except ValueError:
            raise ClientError('Сервер мекенжайы немесе порты жарамсыз.') from None
        try:
            local = ipaddress.ip_address(parsed.hostname or '').is_loopback
        except ValueError:
            local = parsed.hostname == 'localhost'
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ClientError('Сервер мекенжайы http:// немесе https:// деп басталуы керек.')
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ClientError('Тек сервер мекенжайы мен портын енгізіңіз.')
        if parsed.scheme == 'http' and not local:
            raise ClientError('Қашықтағы серверге HTTPS арқылы қосылыңыз.')
        self.url = url.rstrip('/')
        self.token = None
        try:
            context = ssl.create_default_context(cafile=ca, cadata=ca_pem)
        except (OSError, ValueError, TypeError):
            raise ClientError('Сенімді сертификатты оқу мүмкін болмады.') from None
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())

    def request(self, path, data=None):
        raw = None if data is None else json.dumps(data, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = Request(self.url + path, data=raw, headers=headers)
        try:
            with self.opener.open(request, timeout=8) as response:
                return json.loads(response.read())
        except HTTPError as error:
            try:
                message = json.loads(error.read()).get('error', 'Сұрау орындалмады.')
            except (ValueError, UnicodeError):
                message = 'Сервер қате қайтарды.'
            raise ClientError(message, error.code) from None
        except (URLError, OSError, ValueError) as error:
            if isinstance(getattr(error, 'reason', None), ssl.SSLCertVerificationError):
                raise ClientError('Сертификат расталмады. Мекенжай мен --ca параметрін тексеріңіз.') from None
            raise ClientError('Сервермен байланыс жоқ. Мекенжай мен сервердің күйін тексеріңіз.') from None

