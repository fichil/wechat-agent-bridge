import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from backend.app.main import create_app
from backend.app.auth import AuthConfig, AuthStore, AuthError, SESSION_SECONDS, digest
from wechat_account_probe.models import aggregate

PASSWORD = 'synthetic-test-password-2026'
ORIGIN = 'https://bridge.example.test'


class AuthStoreTests(unittest.TestCase):
    def test_password_policy_accepts_4_and_128_characters(self):
        store = AuthStore(':memory:')
        try:
            for value in ('abc', 'x'*129):
                with self.assertRaises(ValueError): store.set_password(value)
            store.set_password('test')
            self.assertIsNotNone(store.login('admin', 'test', 'short')[1])
            store.set_password('x'*128, reset=True)
            self.assertIsNotNone(store.login('admin', 'x'*128, 'long')[1])
        finally:
            store.close()

    def test_persistence_hashes_expiry_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'auth.sqlite3'
            clock = [1000.0]
            store = AuthStore(path, clock=lambda: clock[0])
            store.set_password(PASSWORD)
            one, session = store.login('admin', PASSWORD, 'one')
            two, _ = store.login('admin', PASSWORD, 'two')
            self.assertEqual(session['expires'], clock[0]+SESSION_SECONDS)
            self.assertEqual(store.db.execute('SELECT token_hash FROM sessions WHERE token_hash=?', (digest(one),)).fetchone()[0], digest(one))
            self.assertNotIn(PASSWORD, str(list(store.db.execute('SELECT * FROM administrator'))))
            store.logout(one)
            self.assertIsNone(store.session(one))
            self.assertIsNotNone(store.session(two))
            store.close()
            store = AuthStore(path, clock=lambda: clock[0])
            self.assertIsNotNone(store.session(two))
            clock[0] += SESSION_SECONDS
            self.assertIsNone(store.session(two))
            three, _ = store.login('admin', PASSWORD, 'three')
            store.set_password(PASSWORD+'new', reset=True)
            self.assertIsNone(store.session(three))
            store.close()

    def test_limits_survive_restart_and_expire(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'auth.sqlite3'
            clock = [1000.0]
            store = AuthStore(path, clock=lambda: clock[0])
            for _ in range(10): store.throttle('same')
            store.close()
            store = AuthStore(path, clock=lambda: clock[0])
            with self.assertRaises(AuthError) as blocked: store.throttle('same')
            self.assertEqual(blocked.exception.status, 429)
            for i in range(20): store.throttle(str(i))
            with self.assertRaises(AuthError): store.throttle('different')
            clock[0] += 901
            store.throttle('same')
            store.close()


class AuthApiTests(unittest.TestCase):
    def setUp(self):
        self.probe = Mock(return_value=aggregate([]))
        self.app = create_app(self.probe, database_path=':memory:', auth_path=':memory:', monitor=False,
                              auth_config=AuthConfig('production', ORIGIN))
        self.client = TestClient(self.app, base_url=ORIGIN, headers={'Origin': ORIGIN})
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def login(self):
        if not self.app.state.auth.initialized(): self.app.state.auth.set_password(PASSWORD)
        response = self.client.post('/api/v1/auth/login', json={'username':'admin','password':PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.client.headers['X-CSRF-Token'] = response.json()['csrf_token']
        return response

    def test_uninitialized_and_all_business_routes_require_authentication(self):
        self.assertEqual(self.client.get('/api/v1/health').status_code, 200)
        state = self.client.post('/api/v1/auth/session', json={}).json()
        self.assertEqual((state['initialized'],state['authenticated']), (False,False))
        for route in self.app.routes:
            if getattr(route, 'path', '').startswith('/api/v1/') and 'POST' in getattr(route, 'methods', {}) and not route.path.startswith('/api/v1/auth/'):
                self.assertEqual(self.client.post(route.path,json={}).status_code,401,route.path)
        self.assertEqual(self.client.get('/docs').status_code,401)
        self.probe.assert_not_called()

    def test_cookie_csrf_tampering_origin_and_no_credentials_in_errors(self):
        response=self.login()
        cookie=response.headers['set-cookie']
        for flag in ('__Host-bridge_session=', 'HttpOnly', 'Secure', 'SameSite=strict', 'Path=/', 'Max-Age=604800'): self.assertIn(flag,cookie)
        self.assertNotIn('Domain=',cookie)
        self.assertEqual(self.client.post('/api/v1/accounts/query',json={}).status_code,200)
        self.assertEqual(self.client.post('/api/v1/accounts/query',json={},headers={'X-CSRF-Token':'bad'}).status_code,403)
        self.assertEqual(self.client.post('/api/v1/accounts/query',json={},headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.post('/api/v1/accounts/query',json={},headers={'Host':'evil.example'}).status_code,400)
        self.assertEqual(self.client.post('/api/v1/accounts/query',json={},headers={'Host':'bridge.example.test:8443'}).status_code,400)
        self.assertEqual(self.client.post('/api/v1/accounts/query',json={},headers={'Host':'localhost'}).status_code,400)
        self.client.cookies.set('__Host-bridge_session','tampered',domain='bridge.example.test',path='/')
        self.assertEqual(self.client.post('/api/v1/accounts/query',json={}).status_code,401)
        failure=self.client.post('/api/v1/auth/login',json={'username':'unknown','password':PASSWORD})
        self.assertEqual(failure.status_code,401)
        self.assertNotIn(PASSWORD,failure.text)

    def test_forwarded_headers_do_not_bypass_tls_or_rate_limit(self):
        self.app.state.auth.set_password(PASSWORD)
        for i in range(10):
            response=self.client.post('/api/v1/auth/login',json={'username':'admin','password':'incorrect'},headers={'X-Forwarded-For':f'10.0.0.{i}'})
            self.assertEqual(response.status_code,401)
        self.assertEqual(self.client.post('/api/v1/auth/login',json={'username':'admin','password':PASSWORD},headers={'X-Forwarded-For':'different'}).status_code,429)
        response=self.client.post('http://bridge.example.test/api/v1/auth/login',json={},headers={'X-Forwarded-Proto':'https'})
        self.assertEqual(response.status_code,403)

    def test_logout_and_password_change_preserve_background_services(self):
        self.login()
        bridge=self.app.state.bridge
        second,_=self.app.state.auth.login('admin',PASSWORD,'another-device')
        self.assertEqual(self.client.post('/api/v1/auth/logout',json={}).status_code,200)
        self.assertIsNotNone(self.app.state.auth.session(second))
        self.assertFalse(bridge.stopped.is_set())
        self.login()
        response=self.client.post('/api/v1/auth/change-password',json={'current_password':PASSWORD,'new_password':PASSWORD+'changed'})
        self.assertEqual(response.status_code,200)
        self.assertIsNone(self.app.state.auth.session(second))
        self.assertFalse(bridge.stopped.is_set())
        self.assertFalse(self.app.state.ai.stopped.is_set())

    def test_change_password_uses_the_same_4_to_128_policy(self):
        self.login()
        for value in ('abc', 'x'*129):
            self.assertEqual(self.client.post('/api/v1/auth/change-password', json={
                'current_password': PASSWORD, 'new_password': value}).status_code, 422)
        self.assertEqual(self.client.post('/api/v1/auth/change-password', json={
            'current_password': PASSWORD, 'new_password': 'test'}).status_code, 200)
        self.assertEqual(self.client.post('/api/v1/auth/login', json={
            'username': 'admin', 'password': 'test'}).status_code, 200)

    def test_websocket_rejected_without_login_and_stops_on_revoke(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('wss://bridge.example.test/api/v1/events'): pass
        self.login()
        with self.client.websocket_connect('wss://bridge.example.test/api/v1/events') as ws:
            ws.send_json({'type':'subscribe','account_ids':[]})
            self.assertEqual(ws.receive_json()['type'],'ready')
            self.client.post('/api/v1/auth/logout',json={})
            self.app.state.events.publish({'type':'changed','resources':['accounts']})
            with self.assertRaises(WebSocketDisconnect) as closed: ws.receive_json()
            self.assertEqual(closed.exception.code,4401)

    def test_expired_websocket_closes_without_events(self):
        self.login()
        with self.client.websocket_connect('wss://bridge.example.test/api/v1/events') as ws:
            ws.send_json({'type':'subscribe','account_ids':[]})
            ws.receive_json()
            self.app.state.auth.clock=lambda:time.time()+SESSION_SECONDS+1
            with self.assertRaises(WebSocketDisconnect) as closed: ws.receive_json()
            self.assertEqual(closed.exception.code,4401)

    def test_password_change_revokes_connected_socket_and_all_devices(self):
        self.login()
        second, _ = self.app.state.auth.login('admin', PASSWORD, 'second')
        with self.client.websocket_connect('wss://bridge.example.test/api/v1/events') as ws:
            ws.send_json({'type':'subscribe','account_ids':[]})
            ws.receive_json()
            self.assertEqual(self.client.post('/api/v1/auth/change-password', json={
                'current_password': PASSWORD, 'new_password': PASSWORD+'updated'}).status_code, 200)
            self.app.state.events.publish({'type':'changed','resources':['accounts']})
            with self.assertRaises(WebSocketDisconnect) as closed: ws.receive_json()
            self.assertEqual(closed.exception.code,4401)
            self.assertIsNone(self.app.state.auth.session(second))

    def test_configuration_does_not_allow_public_development_or_plaintext_production(self):
        for mode,origin in [('production','http://bridge.example.test'),('development','http://0.0.0.0:5173'),('production','https://localhost')]:
            with self.assertRaises(ValueError): AuthConfig(mode,origin)


if __name__=='__main__': unittest.main()
