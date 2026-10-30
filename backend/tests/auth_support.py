"""Business regression clients authenticate through the real login endpoint."""
from fastapi.testclient import TestClient as BaseClient
from backend.app.main import create_app as real_create_app
from backend.app.auth import AuthConfig

PASSWORD = 'synthetic-test-password-2026'


def create_app(*args, **kwargs):
    kwargs.setdefault('auth_path', ':memory:')
    kwargs.setdefault('auth_config', AuthConfig('development', 'http://127.0.0.1:5173', ('http://127.0.0.1:8765',)))
    return real_create_app(*args, **kwargs)


class TestClient(BaseClient):
    def __enter__(self):
        super().__enter__()
        self.headers['Origin'] = 'http://127.0.0.1:5173'
        if not self.app.state.auth.initialized():
            self.app.state.auth.set_password(PASSWORD)
        response = self.post('/api/v1/auth/login', json={'username': 'admin', 'password': PASSWORD})
        assert response.status_code == 200, response.text
        self.headers['X-CSRF-Token'] = response.json()['csrf_token']
        return self
