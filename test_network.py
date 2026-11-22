"""Real HTTPS tests for multiple users and independent device sessions."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import secrets
import tempfile
import threading
import unittest

from api import Client, ClientError
from lan_server import create_server
from network_config import load_profile


class NetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.root = Path(cls.folder.name)
        cls.server = create_server('127.0.0.1', 0, cls.root / 'messages.db', cls.root / 'network')
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.profile = load_profile(cls.root / 'network' / 'connection.json')

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.folder.cleanup()

    def setUp(self):
        with self.server.limit_lock:
            self.server.limits.clear()

    def client(self):
        return Client(**self.profile)

    def register(self, name):
        client = self.client()
        response = client.request('/register', {'username': name, 'name': name,
                                               'password': 'Network-test-password'})
        client.token = response['token']
        return client, response['user']['id']

    def assert_status(self, status, operation):
        with self.assertRaises(ClientError) as raised:
            operation()
        self.assertEqual(raised.exception.status, status)

    def test_profile_uses_tls_with_certificate_validation(self):
        self.assertEqual(self.client().request('/health')['service'], 'Noname Messenger')
        self.assertNotIn('PRIVATE KEY', (self.root / 'network' / 'connection.json').read_text())
        with self.assertRaises(ClientError):
            Client(self.profile['url']).request('/health')
        wrong = create_server('127.0.0.1', 0, self.root / 'wrong.db', self.root / 'wrong')
        wrong.server_close()
        wrong_profile = load_profile(self.root / 'wrong' / 'connection.json')
        with self.assertRaises(ClientError):
            Client(self.profile['url'], ca_pem=wrong_profile['ca_pem']).request('/health')
        # Same bound server, but an IP absent from its certificate's SAN.
        wrong_host = self.profile['url'].replace('127.0.0.1', '127.0.0.2')
        with self.assertRaises(ClientError):
            Client(wrong_host, ca_pem=self.profile['ca_pem']).request('/health')

    def test_same_account_on_two_devices_and_edit_delete_sync(self):
        alice, alice_id = self.register('alice_devices')
        bob, bob_id = self.register('bob_devices')
        alice_second = self.client()
        login = alice_second.request('/login', {'username': 'alice_devices', 'password': 'Network-test-password'})
        alice_second.token = login['token']
        self.assertNotEqual(alice.token, alice_second.token)
        payload = {'recipient': bob_id, 'body': 'Бірінші құрылғыдан сәлем', 'nonce': secrets.token_urlsafe(24)}
        message_id = alice.request('/messages', payload)['id']
        self.assertEqual(bob.request(f'/sync?peer={alice_id}')['messages'][0]['id'], message_id)
        self.assertEqual(alice_second.request(f'/sync?peer={bob_id}')['messages'][0]['body'], payload['body'])
        alice_second.request('/messages/edit', {'id': message_id, 'body': 'Екінші құрылғыда түзетілді'})
        self.assertEqual(bob.request(f'/sync?peer={alice_id}')['messages'][0]['body'], 'Екінші құрылғыда түзетілді')
        alice.request('/messages/delete', {'id': message_id})
        self.assertEqual(bob.request(f'/sync?peer={alice_id}')['messages'], [])
        alice_second.request('/sync')
        alice.request('/logout', {})
        self.assert_status(401, lambda: alice.request('/sync'))
        self.assertTrue(next(user for user in bob.request('/sync')['users'] if user['id'] == alice_id)['online'])
        self.assertIsInstance(alice_second.request('/sync')['users'], list)
        bob.request('/messages', {'recipient': alice_id, 'body': 'Екінші құрылғыға жауап',
                                  'nonce': secrets.token_urlsafe(24)})
        self.assertEqual(alice_second.request(f'/sync?peer={bob_id}')['messages'][0]['body'], 'Екінші құрылғыға жауап')

    def test_concurrent_clients_have_consistent_history(self):
        sender, sender_id = self.register('parallel_sender')
        receiver, receiver_id = self.register('parallel_receiver')
        base = {'recipient': receiver_id, 'body': 'Бір рет қана сақталуы керек', 'nonce': secrets.token_urlsafe(24)}
        def send(_index):
            clone = self.client()
            clone.token = sender.token
            return clone.request('/messages', base)['id']
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(send, range(8)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(receiver.request(f'/sync?peer={sender_id}')['messages']), 1)

    def test_server_reuses_certificate_and_refuses_changed_address(self):
        before = (self.root / 'network' / 'server.key').read_bytes()
        another = create_server('127.0.0.1', 0, self.root / 'messages.db', self.root / 'network')
        another.server_close()
        self.assertEqual(before, (self.root / 'network' / 'server.key').read_bytes())
        with self.assertRaises(ValueError):
            create_server('192.168.40.99', 0, self.root / 'messages.db', self.root / 'network')

    def test_invalid_profiles_and_addresses(self):
        path = self.root / 'invalid.json'
        cases = [[], {}, {'version': True},
                 {'version': 1, 'url': 'http://127.0.0.1', 'ca_pem': self.profile['ca_pem']},
                 {'version': 1, 'url': 'https://127.0.0.1:999999', 'ca_pem': self.profile['ca_pem']},
                 {'version': 1, 'url': 'https://example.com/path', 'ca_pem': self.profile['ca_pem']},
                 {'version': 1, 'url': 'https://example.com', 'ca_pem': '-----BEGIN CERTIFICATE-----\ninvalid'},
                 {'version': 1, 'url': 'https://example.com', 'ca_pem': self.profile['ca_pem'] + 'PRIVATE KEY'}]
        for case in cases:
            path.write_text(json.dumps(case), encoding='utf-8')
            with self.subTest(case=case):
                with self.assertRaises(ClientError):
                    load_profile(path)
        path.write_bytes(b'{' * 70000)
        with self.assertRaises(ClientError):
            load_profile(path)
        for address in ('0.0.0.0', 'localhost', 'not-an-ip', '224.1.2.3'):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    create_server(address, 0, self.root / 'unused.db', self.root / 'unused')


if __name__ == '__main__':
    unittest.main(verbosity=2)
