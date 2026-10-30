"""Loopback-only Caddy acceptance. Synthetic auth/data; never touches WeChat."""
import argparse
from contextlib import asynccontextmanager
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from backend.app.auth import AuthConfig
from backend.app.main import create_app
from wechat_account_probe.models import aggregate
from production_server import render_config
from windows_job import WindowsJob
import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--caddy', required=True)
    parser.add_argument('--directory', required=True)
    args = parser.parse_args()
    directory = Path(args.directory).resolve()
    if not directory.is_relative_to(ROOT / '.local'):
        raise ValueError('Acceptance output must stay in .local')
    directory.mkdir(parents=True, exist_ok=True)
    content = render_config('https://bridge.example.test', ROOT / 'frontend/dist')
    production = directory / 'Caddyfile.production'
    production.write_text(content, encoding='utf-8')
    # Validate the unmodified deployment template before the local TLS fixture.
    subprocess.run([args.caddy, 'validate', '--config', str(production), '--adapter', 'caddyfile'], check=True)
    content = content.replace('admin off', 'admin off\n    auto_https disable_redirects\n    skip_install_trust')
    content = content.replace('bridge.example.test', 'bridge.example.test:19443')
    content = content.replace('bridge.example.test:19443 {', 'https://bridge.example.test:19443 {\n    bind 127.0.0.1\n    tls internal')
    content = content.replace('127.0.0.1:8765', '127.0.0.1:18766')
    config = directory / 'Caddyfile.local'
    config.write_text(content, encoding='utf-8')
    app = create_app(lambda: aggregate([]), database_path=':memory:', auth_path=':memory:', monitor=False,
                     auth_config=AuthConfig('production', 'https://bridge.example.test:19443'))
    business = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with business(application):
            application.state.auth.set_password('synthetic-https-password')
            yield
    app.router.lifespan_context = lifespan
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=18766, access_log=False,
                                          forwarded_allow_ips='127.0.0.1'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    job = WindowsJob()
    child = None
    try:
        for _ in range(200):
            if server.started: break
            if not thread.is_alive(): raise RuntimeError('Synthetic backend failed to start')
            time.sleep(.05)
        else: raise RuntimeError('Synthetic backend startup timeout')
        environment = {**os.environ, 'APPDATA': str(directory), 'LOCALAPPDATA': str(directory),
                       'XDG_DATA_HOME': str(directory / 'data'), 'XDG_CONFIG_HOME': str(directory / 'config')}
        child = subprocess.Popen([sys.executable, str(ROOT / 'scripts/service_child.py'), args.caddy,
                                  'run', '--config', str(config), '--adapter', 'caddyfile'],
                                 stdin=subprocess.PIPE, env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
        job.assign(child)
        child.stdin.write(b'start\n')
        child.stdin.close()
        while not (directory / 'stop.request').exists():
            if child.poll() is not None or not thread.is_alive(): raise RuntimeError('Acceptance service exited')
            time.sleep(.1)
    finally:
        job.close()
        if child: child.wait(timeout=5)
        server.should_exit = True
        thread.join(timeout=15)


if __name__ == '__main__': main()
