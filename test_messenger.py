"""Integration tests using a real local HTTP server and an isolated database."""
import hashlib
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest

from api import Client, ClientError
from server import MessengerServer


class MessengerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.path = Path(cls.folder.name) / 'test.sqlite3'
        cls.server = MessengerServer(('127.0.0.1', 0), cls.path)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        cls.clients = []
        cls.accounts = []
        for username, name in [('nur', 'Нұрмухамед'), ('quat', 'Қуат'), ('third', 'Үшінші')]:
            client = Client(cls.url)
            account = client.request('/register', {'username': username, 'name': name, 'password': 'Study-password-2026'})
            client.token = account['token']
            cls.clients.append(client)
            cls.accounts.append(account)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.folder.cleanup()

    def setUp(self):
        with self.server.limit_lock:
            self.server.limits.clear()

    def assert_status(self, status, operation):
        with self.assertRaises(ClientError) as caught:
            operation()
        self.assertEqual(caught.exception.status, status)

    def send(self, body='Сәлем! Қалың қалай? 👋', nonce=None):
        data = {'recipient': self.accounts[1]['user']['id'], 'body': body,
                'nonce': nonce or secrets.token_urlsafe(20)}
        return self.clients[0].request('/messages', data), data

    def test_registration_and_duplicate_validation(self):
        self.assert_status(409, lambda: Client(self.url).request('/register',
            {'username': 'NUR', 'name': 'Қайталау', 'password': 'Study-password-2026'}))
        self.assert_status(400, lambda: Client(self.url).request('/register',
            {'username': 'short', 'name': 'Аты', 'password': '123'}))
        self.assert_status(400, lambda: Client(self.url).request('/register', []))

    def test_login_passwords_and_tokens(self):
        result = Client(self.url).request('/login', {'username': 'NUR', 'password': 'Study-password-2026'})
        self.assertEqual(result['user']['name'], 'Нұрмухамед')
        self.assert_status(401, lambda: Client(self.url).request('/login', {'username': 'nur', 'password': 'wrong'}))
        self.assert_status(401, lambda: Client(self.url).request('/login', {'username': 'unknown', 'password': 'wrong'}))
        with self.server.store.connect() as db:
            users = db.execute('SELECT salt,password_hash FROM users').fetchall()
            self.assertEqual(len({u['salt'] for u in users}), len(users))
            self.assertNotIn('Study-password-2026', [u['password_hash'] for u in users])
            digests = [r[0] for r in db.execute('SELECT digest FROM sessions')]
            self.assertNotIn(result['token'], digests)
            self.assertIn(hashlib.sha256(result['token'].encode()).hexdigest(), digests)

    def test_exchange_unicode_and_read_counts(self):
        message, data = self.send()
        bob = self.clients[1]
        alice_id = self.accounts[0]['user']['id']
        before = bob.request('/sync')
        self.assertGreaterEqual(next(u for u in before['users'] if u['id'] == alice_id)['unread'], 1)
        received = bob.request(f'/sync?peer={alice_id}&after={message["id"] - 1}')
        self.assertEqual(received['messages'][0]['body'], data['body'])
        self.assertEqual(received['messages'][0]['sender'], alice_id)
        after = bob.request('/sync')
        self.assertEqual(next(u for u in after['users'] if u['id'] == alice_id)['unread'], 0)

    def test_no_access_to_other_peoples_conversation(self):
        sent, _ = self.send('Жеке хат алмасу')
        third = self.clients[2]
        for peer in self.accounts[:2]:
            result = third.request(f'/sync?peer={peer["user"]["id"]}')
            self.assertNotIn(sent['id'], [m['id'] for m in result['messages']])
            self.assertEqual(result['messages'], [])
        self.assert_status(401, lambda: Client(self.url).request('/sync'))
        invalid = Client(self.url)
        invalid.token = 'invalid'
        self.assert_status(401, lambda: invalid.request('/sync'))

    def test_idempotent_retry_and_sender_cannot_be_forged(self):
        result, data = self.send('Қайталамау')
        data['sender'] = self.accounts[2]['user']['id']
        self.assertEqual(self.clients[0].request('/messages', data), result)
        with self.server.store.connect() as db:
            row = db.execute('SELECT sender,count(*) AS total FROM messages WHERE nonce=?', (data['nonce'],)).fetchone()
            self.assertEqual(row['sender'], self.accounts[0]['user']['id'])
            self.assertEqual(row['total'], 1)
        data['body'] = 'Өзгертілді'
        self.assert_status(409, lambda: self.clients[0].request('/messages', data))

    def test_pagination_and_read_boundaries(self):
        alice = self.accounts[0]['user']['id']
        bob = self.accounts[1]['user']['id']
        with self.server.store.connect() as db:
            start = db.execute('SELECT coalesce(max(id),0) FROM messages').fetchone()[0]
            db.executemany('INSERT INTO messages(sender,recipient,body,sent,nonce) VALUES(?,?,?,?,?)',
                           [(alice, bob, f'Хабарлама {i}', time.time(), secrets.token_urlsafe(20)) for i in range(105)])
        first = self.clients[1].request(f'/sync?peer={alice}&after={start}')
        self.assertEqual(len(first['messages']), 100)
        self.assertTrue(first['has_more'])
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages WHERE id>? AND read_at IS NULL', (start,)).fetchone()[0], 5)
        second = self.clients[1].request(f'/sync?peer={alice}&after={first["messages"][-1]["id"]}')
        self.assertEqual(len(second['messages']), 5)
        self.assertFalse(second['has_more'])

    def test_message_validation(self):
        base = {'recipient': self.accounts[1]['user']['id'], 'body': 'x', 'nonce': secrets.token_urlsafe(20)}
        for field, value in [('body', ' '), ('body', 'x' * 4001), ('recipient', True),
                             ('recipient', 0), ('nonce', 'short')]:
            self.assert_status(400, lambda f=field, v=value: self.clients[0].request('/messages', {**base, f: v}))
        self.assert_status(404, lambda: self.clients[0].request('/messages', {**base, 'recipient': 999999}))
        self.assert_status(400, lambda: self.clients[0].request('/sync?peer=-1'))

    def test_expiry_and_logout(self):
        client = Client(self.url)
        account = client.request('/login', {'username': 'nur', 'password': 'Study-password-2026'})
        client.token = account['token']
        client.request('/logout', {})
        self.assert_status(401, lambda: client.request('/sync'))
        account = client.request('/login', {'username': 'nur', 'password': 'Study-password-2026'})
        client.token = account['token']
        with self.server.store.connect() as db:
            db.execute('UPDATE sessions SET expires=0 WHERE digest=?', (hashlib.sha256(client.token.encode()).hexdigest(),))
        self.assert_status(401, lambda: client.request('/sync'))

    def test_login_rate_limit(self):
        with self.server.limit_lock:
            self.server.limits[('auth', '127.0.0.1')].extend([time.monotonic()] * 12)
        self.assert_status(429, lambda: Client(self.url).request('/login', {'username': 'nur', 'password': 'wrong'}))

    def test_remote_plain_http_rejected(self):
        for url in ['http://192.168.1.20:8765', 'ftp://localhost', 'http://user:pass@localhost', 'https://example.com/path']:
            with self.assertRaises(ClientError):
                Client(url)

    def test_sender_edits_and_deletes(self):
        result, data = self.send('Алғашқы мәтін')
        mid = result['id']
        other, owner = self.clients[1], self.clients[0]
        alice = self.accounts[0]['user']['id']
        self.assert_status(404, lambda: other.request('/messages/edit', {'id': mid, 'body': 'Басқа мәтін'}))
        self.assert_status(404, lambda: other.request('/messages/delete', {'id': mid}))
        owner.request('/messages/edit', {'id': mid, 'body': 'Түзетілген мәтін'})
        path = f'/sync?peer={alice}&after={mid - 1}'
        self.assertEqual(other.request(path)['messages'][0]['body'], 'Түзетілген мәтін')
        owner.request('/messages/delete', {'id': mid})
        self.assertEqual(other.request(path)['messages'], [])
        self.assert_status(410, lambda: owner.request('/messages', data))

    def test_database_reopens_with_history(self):
        result, _ = self.send('Дискіге сақталды')
        # A second server instance opens the same database, as after a restart.
        other = MessengerServer(('127.0.0.1', 0), self.path)
        thread = threading.Thread(target=other.serve_forever, daemon=True)
        thread.start()
        try:
            client = Client(f'http://127.0.0.1:{other.server_port}')
            client.token = self.clients[1].token
            result2 = client.request(f'/sync?peer={self.accounts[0]["user"]["id"]}&after={result["id"] - 1}')
            self.assertEqual(result2['messages'][0]['body'], 'Дискіге сақталды')
        finally:
            other.shutdown()
            other.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main(verbosity=2)

