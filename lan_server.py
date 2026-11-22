"""Start an HTTPS server shared by multiple desktop clients on a LAN.

create_server(address, port, db_path, config_dir) returns a configured but not
serving MessengerServer. config_dir/connection.json is the only file to share
with client devices. server.key stays on the server computer.
"""
import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from server import MessengerServer


def validate_address(address):
    try:
        value = ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
        raise ValueError('Сервер компьютерінің IPv4 мекенжайын көрсетіңіз, мысалы 192.168.1.20.') from None
    if value.is_unspecified or value.is_multicast or value.is_reserved or str(value) == '255.255.255.255':
        raise ValueError('Құрылғылар қосыла алатын IPv4 мекенжайын көрсетіңіз.')
    return value


def write_exclusive(path, data):
    """Never overwrite an existing key or half-written certificate set."""
    with path.open('xb') as stream:
        stream.write(data)


def ensure_certificates(address, config_dir):
    """Reuse the existing identity, refusing address changes or partial files."""
    config_dir.mkdir(parents=True, exist_ok=True)
    ca_path = config_dir / 'ca.pem'
    cert_path = config_dir / 'server.pem'
    key_path = config_dir / 'server.key'
    paths = (ca_path, cert_path, key_path)
    if any(path.exists() for path in paths):
        if not all(path.is_file() for path in paths):
            raise ValueError('Сертификат файлдары толық емес. Жаңа --config-dir бумасын қолданыңыз.')
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if address not in san.get_values_for_type(x509.IPAddress):
            raise ValueError('Мекенжай сертификатқа сәйкес келмейді. Жаңа --config-dir бумасын қолданыңыз және жаңа профильді таратыңыз.')
        if certificate.not_valid_after_utc <= datetime.now(timezone.utc):
            raise ValueError('Сертификат мерзімі аяқталды. Жаңа --config-dir бумасын қолданыңыз.')
        return ca_path, cert_path, key_path

    # An exclusive lock keeps two server launches from creating mixed identities.
    lock_path = config_dir / '.certificate-init.lock'
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('Сертификат әзірленіп жатыр. Біраздан кейін қайталаңыз.') from None
    os.close(descriptor)
    try:
        if any(path.exists() for path in paths):
            raise ValueError('Сертификат файлдары өзгерді. Серверді қайта іске қосыңыз.')
        now = datetime.now(timezone.utc)
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Noname Messenger LAN CA')])
        root = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
                .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                               key_encipherment=False, data_encipherment=False, key_agreement=False,
                               key_cert_sign=True, crl_sign=True, encipher_only=None, decipher_only=None), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
                .sign(ca_key, hashes.SHA256()))
        server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, str(address))])
        sans = [x509.IPAddress(address), x509.DNSName('localhost')]
        if address != ipaddress.IPv4Address('127.0.0.1'):
            sans.append(x509.IPAddress(ipaddress.IPv4Address('127.0.0.1')))
        leaf = (x509.CertificateBuilder().subject_name(name).issuer_name(ca_name)
                .public_key(server_key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName(sans), critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                               key_encipherment=True, data_encipherment=False, key_agreement=False,
                               key_cert_sign=False, crl_sign=False, encipher_only=None, decipher_only=None), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                .sign(ca_key, hashes.SHA256()))
        key_bytes = server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption())
        write_exclusive(key_path, key_bytes)
        if os.name != 'nt':
            key_path.chmod(0o600)
        write_exclusive(cert_path, leaf.public_bytes(serialization.Encoding.PEM))
        write_exclusive(ca_path, root.public_bytes(serialization.Encoding.PEM))
    finally:
        lock_path.unlink(missing_ok=True)
    return ca_path, cert_path, key_path


def create_server(address, port, db_path, config_dir):
    """Create a TLS server on IPv4 interfaces and export its public profile."""
    address = validate_address(address)
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('Порт 1–65535 аралығында болуы керек.')
    config_dir = Path(config_dir)
    ca_path, cert_path, key_path = ensure_certificates(address, config_dir)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    server = MessengerServer(('0.0.0.0', port), db_path)
    try:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        profile = {'version': 1, 'url': f'https://{address}:{server.server_port}',
                   'ca_pem': ca_path.read_text(encoding='ascii')}
        temporary = config_dir / 'connection.json.tmp'
        temporary.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(config_dir / 'connection.json')
    except Exception:
        server.server_close()
        raise
    return server


def main():
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description='Бірнеше құрылғыға арналған Noname Messenger сервері')
    parser.add_argument('--address', required=True, help='Осы компьютердің жергілікті IPv4 мекенжайы')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db', type=Path, default=base / 'data' / 'messages.sqlite3')
    parser.add_argument('--config-dir', type=Path, default=base / 'data' / 'network')
    args = parser.parse_args()
    try:
        if not 1 <= args.port <= 65535:
            raise ValueError('Порт 1–65535 аралығында болуы керек.')
        server = create_server(args.address, args.port, args.db, args.config_dir)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Сервер іске қосылмады: {error}\n')
    print(f'Noname Messenger сервері: https://{args.address}:{server.server_port}', flush=True)
    print(f'Басқа құрылғыларға тек мына файлды жіберіңіз: {args.config_dir / "connection.json"}', flush=True)
    print('Барлық құрылғыларды бір Wi-Fi немесе жергілікті желіге қосыңыз.', flush=True)
    print('Сервер ашық тұрсын. Тоқтату үшін Ctrl+C басыңыз.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
