"""Read the server profile shared by the LAN administrator.

Only the public CA certificate and server URL are imported. The certificate
becomes trusted for this client connection, so obtain this file directly from
the person running the server.
"""
import json
from pathlib import Path

from api import Client, ClientError

PROFILE_LIMIT = 65536


def load_profile(path):
    """Return {'url': ..., 'ca_pem': ...} after validating a connection.json."""
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(PROFILE_LIMIT + 1)
        if len(raw) > PROFILE_LIMIT:
            raise ValueError()
        data = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1:
            raise ValueError()
        url, ca_pem = data.get('url'), data.get('ca_pem')
        if not isinstance(url, str) or not url.startswith('https://'):
            raise ValueError()
        if not isinstance(ca_pem, str) or not ca_pem.startswith('-----BEGIN CERTIFICATE-----'):
            raise ValueError()
        if 'PRIVATE KEY' in ca_pem:
            raise ValueError()
        # Client validates URL shape, port, PEM format and certificate trust setup.
        client = Client(url, ca_pem=ca_pem)
        return {'url': client.url, 'ca_pem': ca_pem}
    except (OSError, ValueError, UnicodeError, ClientError):
        raise ClientError('Қосылу профилі жарамсыз. Сервер иесінен connection.json файлын алыңыз.') from None
