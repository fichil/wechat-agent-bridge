"""Supervise one loopback backend and Caddy in the logged-in WeChat user's session."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.auth import AuthConfig, AuthStore, DEFAULT_AUTH_PATH
from backend.app.ai_timeouts import AI_SHUTDOWN_GRACE_SECONDS

from bridge_paths import DATA, FRONTEND
RUNTIME = DATA / 'production'


def render_config(origin, dist):
    config = AuthConfig('production', origin)
    u = urlsplit(config.origin)
    # Reject Caddy syntax and international names not normalized by DNS tooling.
    import re
    if not re.fullmatch(r'[A-Za-z0-9.-]+(?::[0-9]{1,5})?', u.netloc) or '.' not in u.hostname:
        raise ValueError('Use an ASCII DNS hostname for BRIDGE_PUBLIC_ORIGIN')
    if u.port is not None and u.port != 443:
        raise ValueError('Production HTTPS uses port 443')
    path = str(Path(dist).resolve()).replace('\\', '/')
    if any(c in path for c in ('"', '\n', '\r', '{', '}')):
        raise ValueError('Unsupported frontend build path')
    return (ROOT/'deploy/Caddyfile.template').read_text(encoding='utf-8').replace('@@SITE@@', u.netloc).replace('@@HOST@@', u.netloc).replace('@@DIST@@', path)


def stop():
    import psutil
    metadata = RUNTIME/'current.json'
    if not metadata.exists():
        raise RuntimeError('No managed production process is recorded')
    value = json.loads(metadata.read_text(encoding='utf-8'))
    process = psutil.Process(value['pid'])
    if abs(process.create_time()-value['created']) > .01 or not any(str(ROOT/'scripts/production_server.py').lower() == arg.lower() for arg in process.cmdline()):
        raise RuntimeError('Recorded process identity changed; nothing stopped')
    stop_file = Path(value['stop_file']).resolve()
    if not stop_file.is_relative_to(RUNTIME.resolve()) or stop_file.name != 'stop.request':
        raise RuntimeError('Invalid stop-file path')
    stop_file.write_text('stop\n', encoding='utf-8')
    print('Graceful stop requested for this production launch only.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--caddy', default=os.environ.get('BRIDGE_CADDY', 'caddy'))
    args = parser.parse_args()
    if args.stop:
        stop()
        return
    if os.name != 'nt' or sys.maxsize <= 2**32:
        raise RuntimeError('Windows x64 is required')
    os.environ['BRIDGE_ENV'] = 'production'
    origin = os.environ.get('BRIDGE_PUBLIC_ORIGIN', '')
    content = render_config(origin, FRONTEND)
    if not (FRONTEND/'index.html').is_file():
        raise RuntimeError('Build frontend first: npm ci; npm run build')
    auth_path = Path(os.environ.get('BRIDGE_AUTH_DATABASE', str(DEFAULT_AUTH_PATH))).resolve()
    if not auth_path.is_file():
        raise RuntimeError('Initialize the administrator first: python -m backend.admin init')
    auth = AuthStore(auth_path)
    try:
        if not auth.initialized(): raise RuntimeError('Initialize the administrator first')
    finally:
        auth.close()
    caddy = shutil.which(args.caddy)
    if not caddy:
        raise RuntimeError('Caddy was not found. Set BRIDGE_CADDY to the official caddy.exe path')
    for host, port in [('127.0.0.1',8765),('0.0.0.0',80),('0.0.0.0',443)]:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try: sock.bind((host,port))
            except OSError: raise RuntimeError(f'Port {port} is occupied; existing service left untouched') from None
    run_dir = RUNTIME/(datetime.now().strftime('%Y%m%d-%H%M%S-')+uuid4().hex[:6])
    run_dir.mkdir(parents=True)
    config_path = run_dir/'Caddyfile'
    config_path.write_text(content, encoding='utf-8')
    caddy_env = {**os.environ, 'APPDATA': str(DATA / 'caddy'), 'XDG_DATA_HOME': str(DATA / 'caddy'),
                 'XDG_CONFIG_HOME': str(DATA / 'caddy-config')}
    subprocess.run([caddy, 'validate', '--config', str(config_path), '--adapter', 'caddyfile'], check=True, env=caddy_env,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    if args.check:
        print('Production configuration, administrator, build and ports are ready. No services started.')
        return
    import psutil
    from windows_job import WindowsJob
    stop_file = run_dir/'stop.request'
    metadata = RUNTIME/'current.json'
    if metadata.exists():
        old=json.loads(metadata.read_text(encoding='utf-8'))
        try:
            if abs(psutil.Process(old['pid']).create_time()-old['created'])<.01:
                raise RuntimeError('A production supervisor is already running')
        except psutil.NoSuchProcess: pass
    children, handles = [], []
    job = WindowsJob()
    metadata.write_text(json.dumps(dict(pid=os.getpid(),created=psutil.Process().create_time(),stop_file=str(stop_file))),encoding='utf-8')
    commands = [
        ('backend',[sys.executable,'-B',str(ROOT/'scripts/serve_backend.py'),'--stop-file',str(stop_file)]),
        ('caddy',[caddy,'run','--config',str(config_path),'--adapter','caddyfile']),
    ]
    try:
        for name, command in commands:
            output=(run_dir/(name+'.log')).open('wb'); handles.append(output)
            child=subprocess.Popen([sys.executable,'-B',str(ROOT/'scripts/service_child.py'),*command],cwd=ROOT,
                stdin=subprocess.PIPE,stdout=output,stderr=output,env=caddy_env if name=='caddy' else None,creationflags=subprocess.CREATE_NO_WINDOW)
            children.append(child); job.assign(child); child.stdin.write(b'start\n'); child.stdin.close()
            if name=='backend':
                opener=build_opener(ProxyHandler({})); deadline=time.monotonic()+40
                while time.monotonic()<deadline:
                    if child.poll() is not None: raise RuntimeError('Backend stopped during startup; see local log')
                    try:
                        with opener.open('http://127.0.0.1:8765/api/v1/health',timeout=1) as result:
                            if json.load(result).get('service')=='wechat-agent-bridge': break
                    except (OSError,ValueError): pass
                    time.sleep(.25)
                else: raise RuntimeError('Backend startup timed out')
        print('Started. Verify HTTPS certificate and login at '+origin,flush=True)
        while not stop_file.exists() and all(p.poll() is None for p in children): time.sleep(.5)
        if not stop_file.exists(): raise RuntimeError('A managed service exited; inspect the local production logs')
    except KeyboardInterrupt:
        pass
    finally:
        stop_file.write_text('stop\n',encoding='utf-8')
        if children and children[0].poll() is None:
            try: children[0].wait(timeout=AI_SHUTDOWN_GRACE_SECONDS)
            except subprocess.TimeoutExpired: print('Backend graceful-stop deadline exceeded',flush=True)
        job.close()
        for child in children:
            if child.poll() is None: child.kill()
            child.wait(timeout=5)
        for handle in handles: handle.close()
        if metadata.exists() and json.loads(metadata.read_text(encoding='utf-8')).get('pid')==os.getpid(): metadata.unlink()


if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(1)
