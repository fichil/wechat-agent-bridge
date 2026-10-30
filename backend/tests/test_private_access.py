from pathlib import Path
import unittest
from fastapi.testclient import TestClient

from backend.app.auth import AuthConfig
from backend.app.main import create_app
from scripts.production_server import render_config
from wechat_account_probe.models import aggregate

ORIGIN = 'http://127.0.0.1:18765'


class PrivateAccessTests(unittest.TestCase):
    def test_private_mode_is_explicit_fixed_origin_and_loopback_only(self):
        for origin in ('http://0.0.0.0:18765','http://47.104.176.159:18765','http://localhost:18765',
                       'http://127.0.0.1:18765/path','http://127.0.0.1:8765'):
            with self.assertRaises(ValueError): AuthConfig('private',origin)
        with self.assertRaises(ValueError): AuthConfig('private',ORIGIN,('http://localhost:18765',))
        with self.assertRaises(ValueError): AuthConfig('production',ORIGIN)
        config = render_config(ORIGIN,Path('frontend/dist'),'private')
        self.assertIn('http://127.0.0.1:18765 {',config)
        self.assertIn('bind 127.0.0.1',config)
        self.assertIn('header_up X-Forwarded-Proto http',config)
        self.assertNotIn('Strict-Transport-Security',config)
        self.assertNotIn('@@',config)

    def test_private_login_keeps_csrf_host_websocket_and_revocation_guards(self):
        app = create_app(lambda:aggregate([]),database_path=':memory:',auth_path=':memory:',monitor=False,
                         auth_config=AuthConfig('private',ORIGIN))
        with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as client:
            app.state.auth.set_password('synthetic-password')
            self.assertEqual(client.post('/api/v1/accounts/query',json={}).status_code,401)
            response = client.post('/api/v1/auth/login',json=dict(username='admin',password='synthetic-password'))
            self.assertEqual(response.status_code,200)
            cookie = response.headers['set-cookie']
            for flag in ('bridge_private_session=','HttpOnly','SameSite=strict'): self.assertIn(flag,cookie)
            self.assertNotIn('Secure',cookie)
            client.headers['X-CSRF-Token'] = response.json()['csrf_token']
            self.assertEqual(client.post('/api/v1/accounts/query',json={}).status_code,200)
            self.assertEqual(client.post('/api/v1/accounts/query',json={},headers={'X-CSRF-Token':'wrong'}).status_code,403)
            self.assertEqual(client.post('/api/v1/accounts/query',json={},headers={'Origin':'http://evil.test'}).status_code,403)
            self.assertEqual(client.post('/api/v1/accounts/query',json={},headers={'Host':'evil.test'}).status_code,400)
            with client.websocket_connect('ws://127.0.0.1:18765/api/v1/events') as ws:
                ws.send_json({'type':'subscribe','account_ids':[]})
                self.assertEqual(ws.receive_json()['type'],'ready')
            app.state.auth.set_password('reset-password',reset=True)
            self.assertEqual(client.post('/api/v1/accounts/query',json={}).status_code,401)
