import unittest
from unittest.mock import Mock, patch
import io
from pathlib import Path
from scripts.production_server import render_config, wait_backend
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.auth import AuthConfig
from wechat_account_probe.models import aggregate


class ProductionConfigTests(unittest.TestCase):
    def test_cold_archive_can_finish_starting_after_three_minutes(self):
        child = Mock(); child.poll.return_value = None
        opener = Mock()
        opener.open.side_effect = [OSError('not listening yet'), io.StringIO('{"service":"wechat-agent-bridge"}')]
        with patch('scripts.production_server.build_opener', return_value=opener), \
             patch('scripts.production_server.time.monotonic', side_effect=[0, 190, 220]), \
             patch('scripts.production_server.time.sleep'):
            wait_backend(child)
        self.assertEqual(opener.open.call_count, 2)

    def test_startup_remains_bounded_and_dead_child_fails_immediately(self):
        with patch('scripts.production_server.time.monotonic', side_effect=[0, 601]):
            with self.assertRaisesRegex(RuntimeError, 'startup timed out'): wait_backend(Mock())
        child = Mock(); child.poll.return_value = 1
        with patch('scripts.production_server.time.monotonic', side_effect=[0, 1]):
            with self.assertRaisesRegex(RuntimeError, 'stopped during startup'): wait_backend(child)

    def test_template_exposes_dist_and_proxies_api_without_opening_backend(self):
        config=render_config('https://bridge.example.com',Path('frontend/dist'))
        self.assertNotIn('@@',config)
        self.assertIn('handle /api/*',config)
        self.assertIn('reverse_proxy 127.0.0.1:8765',config)
        self.assertIn('try_files {path} /index.html',config)
        self.assertIn('header_up X-Forwarded-For {remote_host}',config)
        self.assertNotIn('.local',config)
        for origin in ['http://bridge.example.com','https://bridge.example.com\n:80','https://bridge.example.com:8443']:
            with self.assertRaises(ValueError):render_config(origin,Path('frontend/dist'))

    def test_only_loopback_proxy_can_supply_https_and_client_identity(self):
        app=create_app(lambda:aggregate([]),database_path=':memory:',auth_path=':memory:',monitor=False,
                       auth_config=AuthConfig('production','https://bridge.example.com'))
        wrapped=ProxyHeadersMiddleware(app,trusted_hosts=['127.0.0.1'])
        with TestClient(wrapped,base_url='http://bridge.example.com',client=('127.0.0.1',50000)) as client:
            app.state.auth.set_password('synthetic-password-2026')
            response=client.post('/api/v1/auth/login',json={'username':'admin','password':'synthetic-password-2026'},
                headers={'Origin':'https://bridge.example.com','X-Forwarded-Proto':'https','X-Forwarded-For':'198.51.100.42'})
            self.assertEqual(response.status_code,200)
            self.assertIn('Secure',response.headers['set-cookie'])
            self.assertEqual(app.state.auth.db.execute('SELECT ip FROM attempts').fetchone()[0],'198.51.100.42')
        with TestClient(wrapped,base_url='http://bridge.example.com',client=('198.51.100.42',50000)) as client:
            response=client.post('/api/v1/auth/session',json={},headers={'Origin':'https://bridge.example.com','X-Forwarded-Proto':'https'})
            self.assertEqual(response.status_code,403)
