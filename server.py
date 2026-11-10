"""Noname Messenger: a small, local-first educational messaging server."""
import argparse
from collections import defaultdict, deque
from contextlib import contextmanager
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import sqlite3
import ssl
import threading
import time
from urllib.parse import parse_qs, urlsplit

MAX_BODY = 32768
PASSWORD_ROUNDS = 600000
SESSION_SECONDS = 86400


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL,
                    last_seen REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, sender INTEGER NOT NULL REFERENCES users(id),
                    recipient INTEGER NOT NULL REFERENCES users(id), body TEXT NOT NULL,
                    sent REAL NOT NULL, read_at REAL,
                    nonce TEXT NOT NULL, UNIQUE(sender, nonce)
                );
                CREATE INDEX IF NOT EXISTS messages_pair ON messages(sender, recipient, id);
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires);
            """)
            columns = {row['name'] for row in db.execute('PRAGMA table_info(messages)')}
            if 'deleted' not in columns:
                db.execute('ALTER TABLE messages ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
        # Equal-cost verification for nonexistent usernames.
        self.dummy_salt = secrets.token_hex(16)
        self.dummy_hash = self.password_hash('dummy-account-password', self.dummy_salt)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def password_hash(password, salt):
        return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), PASSWORD_ROUNDS).hex()

    @staticmethod
    def public(user):
        return {key: user[key] for key in ('id', 'username', 'name')}

    def session(self, db, user):
        token = secrets.token_urlsafe(32)
        db.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
        db.execute('INSERT INTO sessions VALUES(?,?,?)',
                   (hashlib.sha256(token.encode()).hexdigest(), user['id'], time.time() + SESSION_SECONDS))
        return {'token': token, 'user': self.public(user)}

    def auth(self, token):
        if not token or len(token) > 200:
            raise APIError(401, 'Тіркелгіге қайта кіріңіз.')
        with self.connect() as db:
            user = db.execute('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id '
                              'WHERE s.digest=? AND s.expires>?',
                              (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            if not user:
                raise APIError(401, 'Сеанс аяқталды. Қайта кіріңіз.')
            db.execute('UPDATE users SET last_seen=? WHERE id=?', (time.time(), user['id']))
            return self.public(user)

    def register(self, data):
        username = field(data, 'username').strip().lower()
        name = field(data, 'name').strip()
        password = field(data, 'password')
        if not re.fullmatch(r'[a-z0-9_]{3,24}', username):
            raise APIError(400, 'Логин 3–24 латын әрпінен, саннан немесе _ таңбасынан тұруы керек.')
        if not name or len(name) > 60 or any(ord(c) < 32 for c in name):
            raise APIError(400, 'Аты 1–60 таңбадан тұруы керек.')
        if not 10 <= len(password) <= 128:
            raise APIError(400, 'Құпиясөз 10–128 таңбадан тұруы керек.')
        salt = secrets.token_hex(16)
        hashed = self.password_hash(password, salt)
        with self.connect() as db:
            try:
                cursor = db.execute('INSERT INTO users(username,name,salt,password_hash) VALUES(?,?,?,?)',
                                    (username, name, salt, hashed))
            except sqlite3.IntegrityError:
                raise APIError(409, 'Бұл логин қолданыста.')
            return self.session(db, db.execute('SELECT * FROM users WHERE id=?', (cursor.lastrowid,)).fetchone())

    def login(self, data):
        username = field(data, 'username').strip().lower()
        password = field(data, 'password')
        if len(password) > 128:
            raise APIError(400, 'Құпиясөз тым ұзын.')
        with self.connect() as db:
            user = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        salt = user['salt'] if user else self.dummy_salt
        expected = user['password_hash'] if user else self.dummy_hash
        valid = hmac.compare_digest(self.password_hash(password, salt), expected)
        if not valid or not user:
            raise APIError(401, 'Логин немесе құпиясөз қате.')
        with self.connect() as db:
            return self.session(db, user)

    def sync(self, user_id, peer, after):
        with self.connect() as db:
            users = db.execute('''
                SELECT u.id,u.username,u.name,u.last_seen,
                (SELECT count(*) FROM messages m WHERE m.sender=u.id AND m.recipient=?
                    AND m.read_at IS NULL AND m.deleted=0) AS unread,
                (SELECT max(id) FROM messages m WHERE m.deleted=0 AND (
                    (m.sender=u.id AND m.recipient=?) OR
                    (m.sender=? AND m.recipient=u.id))) AS last_id
                FROM users u WHERE u.id != ? ORDER BY last_id DESC,u.name LIMIT 500
            ''', (user_id, user_id, user_id, user_id)).fetchall()
            messages = []
            if peer:
                if not db.execute('SELECT 1 FROM users WHERE id=?', (peer,)).fetchone():
                    raise APIError(404, 'Пайдаланушы табылмады.')
                messages = db.execute('''SELECT id,sender,recipient,body,sent FROM messages
                    WHERE deleted=0 AND id>? AND ((sender=? AND recipient=?) OR (sender=? AND recipient=?))
                    ORDER BY id LIMIT 100''', (after, user_id, peer, peer, user_id)).fetchall()
                # Mark only delivered messages, not newer messages omitted by the page limit.
                if messages:
                    db.execute('UPDATE messages SET read_at=? WHERE sender=? AND recipient=? AND id<=?',
                               (time.time(), peer, user_id, messages[-1]['id']))
            return {'users': [{**dict(u), 'online': time.time() - u['last_seen'] < 15} for u in users],
                    'messages': [dict(m) for m in messages], 'has_more': len(messages) == 100}

    def send(self, user_id, data):
        peer = data.get('recipient')
        body = field(data, 'body').strip()
        nonce = field(data, 'nonce')
        if type(peer) is not int or peer <= 0 or peer == user_id:
            raise APIError(400, 'Басқа пайдаланушыны таңдаңыз.')
        if not body or len(body) > 4000:
            raise APIError(400, 'Хабарлама 1–4000 таңбадан тұруы керек.')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}', nonce):
            raise APIError(400, 'Хабарлама идентификаторы жарамсыз.')
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM users WHERE id=?', (peer,)).fetchone():
                raise APIError(404, 'Пайдаланушы табылмады.')
            db.execute('INSERT OR IGNORE INTO messages(sender,recipient,body,sent,nonce) VALUES(?,?,?,?,?)',
                       (user_id, peer, body, time.time(), nonce))
            message = db.execute('SELECT * FROM messages WHERE sender=? AND nonce=?', (user_id, nonce)).fetchone()
            if message['deleted']:
                raise APIError(410, 'Хабарлама жойылған.')
            if message['recipient'] != peer or message['body'] != body:
                raise APIError(409, 'Хабарлама идентификаторы бұрын қолданылған.')
            return {'id': message['id']}


def field(data, key):
    value = data.get(key)
    if not isinstance(value, str):
        raise APIError(400, 'Өріс жарамсыз: ' + key)
    return value


class MessengerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, path):
        self.store = Store(path)
        self.limits = defaultdict(deque)
        self.limit_lock = threading.Lock()
        super().__init__(address, Handler)

    def throttle(self, key, limit, window=60):
        now = time.monotonic()
        with self.limit_lock:
            # Bound retained identities and remove idle counters.
            if len(self.limits) > 10000:
                for old in list(self.limits):
                    if not self.limits[old] or self.limits[old][-1] < now - 60:
                        del self.limits[old]
            queue = self.limits[key]
            while queue and queue[0] <= now - window:
                queue.popleft()
            if len(queue) >= limit:
                raise APIError(429, 'Сұрау тым көп. Бір минут күтіңіз.')
            queue.append(now)


class Handler(BaseHTTPRequestHandler):
    server_version = 'Qanat/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_args):
        pass  # Do not log usernames, message bodies or session tokens.

    def do_GET(self):
        self.handle_api('GET')

    def do_POST(self):
        self.handle_api('POST')

    def reply(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def handle_api(self, method):
        try:
            path = urlsplit(self.path)
            self.server.throttle(('all', self.client_address[0]), 1000)
            data = {}
            if method == 'POST':
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0 or length > MAX_BODY:
                    raise APIError(413, 'Сұрау көлемі жарамсыз.')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise APIError(400, 'JSON нысаны қажет.')
            if method == 'GET' and path.path == '/health':
                return self.reply(200, {'service': 'Noname Messenger', 'version': 1})
            if method == 'POST' and path.path in ('/register', '/login'):
                self.server.throttle(('auth', self.client_address[0]), 12)
                operation = self.server.store.register if path.path == '/register' else self.server.store.login
                return self.reply(200, operation(data))
            token = self.headers.get('Authorization', '').removeprefix('Bearer ')
            user = self.server.store.auth(token)
            if method == 'POST' and path.path == '/logout':
                with self.server.store.connect() as db:
                    db.execute('DELETE FROM sessions WHERE digest=?', (hashlib.sha256(token.encode()).hexdigest(),))
                    db.execute('UPDATE users SET last_seen=0 WHERE id=?', (user['id'],))
                return self.reply(200, {'ok': True})
            if method == 'GET' and path.path == '/sync':
                query = parse_qs(path.query)
                peer = int(query.get('peer', ['0'])[0])
                after = int(query.get('after', ['0'])[0])
                if peer < 0 or after < 0:
                    raise ValueError()
                return self.reply(200, self.server.store.sync(user['id'], peer, after))
            if method == 'POST' and path.path == '/messages':
                self.server.throttle(('send', user['id']), 120)
                return self.reply(200, self.server.store.send(user['id'], data))
            if method == 'POST' and path.path in ('/messages/edit', '/messages/delete'):
                mid = data.get('id')
                if type(mid) is not int or mid < 1:
                    raise APIError(400, 'Хабарлама нөмірі жарамсыз.')
                with self.server.store.connect() as db:
                    if path.path.endswith('/edit'):
                        body = field(data, 'body').strip()
                        if not 1 <= len(body) <= 4000:
                            raise APIError(400, 'Хабарлама 1–4000 таңбадан тұруы керек.')
                        changed = db.execute('UPDATE messages SET body=? WHERE id=? AND sender=? AND deleted=0',
                                             (body, mid, user['id']))
                    else:
                        changed = db.execute("UPDATE messages SET body='',deleted=1 WHERE id=? AND sender=? AND deleted=0",
                                             (mid, user['id']))
                    if not changed.rowcount:
                        raise APIError(404, 'Өзіңіз жіберген хабарлама табылмады.')
                return self.reply(200, {'ok': True})
            raise APIError(404, 'Сұрау белгісіз.')
        except APIError as error:
            self.reply(error.status, {'error': error.message})
        except (ValueError, UnicodeError, OverflowError):
            self.reply(400, {'error': 'Сұрау жарамсыз.'})
        except (ConnectionError, TimeoutError):
            pass
        except Exception:
            self.reply(500, {'error': 'Сервер қатесі. Қайталап көріңіз.'})


def main():
    parser = argparse.ArgumentParser(description='Noname Messenger server')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db', type=Path, default=Path(__file__).parent / 'data' / 'messages.sqlite3')
    parser.add_argument('--cert', help='TLS certificate PEM')
    parser.add_argument('--key', help='TLS private key PEM')
    args = parser.parse_args()
    if bool(args.cert) != bool(args.key):
        parser.error('--cert және --key бірге көрсетіледі')
    if args.host not in ('127.0.0.1', 'localhost', '::1') and not args.cert:
        parser.error('Сыртқы қосылымға TLS қажет: --cert және --key')
    server = MessengerServer((args.host, args.port), args.db)
    if args.cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.cert, args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"Noname Messenger: {'https' if args.cert else 'http'}://{args.host}:{server.server_port}")
    print('Тоқтату үшін Ctrl+C басыңыз.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

