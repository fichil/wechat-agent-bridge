"""Authenticated local admin client; traffic never leaves loopback."""
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
import httpx


class LocalApi:
    def __init__(self, origin, username, password):
        self.client = httpx.Client(base_url='http://127.0.0.1:8765', timeout=30, trust_env=False,
                                   headers={'Host': urlsplit(origin).netloc, 'Origin': origin,
                                            'X-Forwarded-Proto': urlsplit(origin).scheme})
        response = self.client.post('/api/v1/auth/login', json=dict(username=username, password=password))
        if response.status_code != 200:
            self.client.close()
            raise ValueError('后台登录失败，请检查启动器中填写的用户名和密码。')
        cookies = SimpleCookie()
        for header in response.headers.get_list('set-cookie'):
            cookies.load(header)
        self.client.headers['Cookie'] = '; '.join(f'{key}={value.value}' for key,value in cookies.items())
        session = self.client.post('/api/v1/auth/session', json={})
        session.raise_for_status()
        self.client.headers['X-CSRF-Token'] = session.json()['csrf_token']

    def post(self, route, **payload):
        response = self.client.post('/api/v1/' + route, json=payload)
        if response.status_code >= 400:
            raise ValueError('本机后台操作失败：' + str(response.json().get('message', response.status_code)))
        return response.json()

    def close(self):
        try:
            self.client.post('/api/v1/auth/logout', json={})
        finally:
            self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
