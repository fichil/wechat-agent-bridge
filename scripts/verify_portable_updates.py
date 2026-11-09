"""Windows acceptance: disposable real packages, authenticated web handoff and restart.

Only the two release fixtures have an in-process local GitHub transport. The
published ZIP is read-only; no test endpoint, alternate feed or crash switch is
included in the customer package. Never use an existing install or personal data.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge_portable import deploy as d
from bridge_portable.local_api import LocalApi
from bridge_portable.update_engine import bootstrap, location
from bridge_portable.update_feed import RELEASES_URL

TRANSPORT = """
# Synthetic transport installed only in disposable acceptance packages.
from pathlib import Path as _Path
_fixture = _Path(FIXTURE_PATH)
def fetch(url, *, output=None, limit=2*1024**2, progress=None):
    allowed_url(url)
    item = json.loads((_fixture/'release.json').read_text(encoding='utf-8'))
    if url == API_URL + '?per_page=100': return json.dumps([item] if item else []).encode()
    if url == API_URL + '/latest': return json.dumps(item).encode()
    if item and url == API_URL + '/tags/' + item['tag_name']: return json.dumps(item).encode()
    asset = next(a for a in item['assets'] if a['browser_download_url'] == url)
    p = _fixture / asset['name']
    assert p.stat().st_size <= limit
    if output is None: return p.read_bytes()
    import shutil
    with p.open('rb') as stream: shutil.copyfileobj(stream, output)
    if progress: progress(p.stat().st_size, p.stat().st_size)
    return b''
"""


def fixture_files(package, feed_dir):
    f = package/'bridge_portable/update_feed.py'
    f.write_text(f.read_text(encoding='utf-8') + TRANSPORT.replace('FIXTURE_PATH',ascii(str(feed_dir))),encoding='utf-8')
    # Interrupt exactly at the atomic pointer switch. The launcher must recover
    # the persisted transaction without a second backup or submission.
    f = package/'bridge_portable/update_engine.py'
    before = "                d.write(self.root / 'current.json', new)"
    code = f.read_text(encoding='utf-8')
    assert code.count(before) == 1
    f.write_text(code.replace(before, before + "\n                crash_gate = Path(" + ascii(str(feed_dir/'interrupt')) + ")\n                if crash_gate.exists():\n                    crash_gate.unlink()\n                    os._exit(77)"),encoding='utf-8')


def manifest(package, version):
    value = d.read(package/'portable-manifest.json')
    value.update(version=version, development=False)
    for name in ('bridge_portable/update_feed.py','bridge_portable/update_engine.py','scripts/production_server.py','WeChatAgentBridge.exe'):
        value['files'][name] = d.digest(package/name)
    d.write(package/'portable-manifest.json', value)
    return value


def publish_fixture(package, feed_dir, version, *, broken=False):
    name = 'WeChatAgentBridge-' + version + '-windows-x64'
    archive = feed_dir/(name+'.zip')
    supervisor = package/'scripts/production_server.py'
    original = supervisor.read_bytes()
    try:
        if broken: supervisor.write_bytes(b"raise RuntimeError('synthetic startup failure')\n" + original)
        m = manifest(package,version)
        with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED,compresslevel=1) as output:
            for path in [*m['files'],'portable-manifest.json']:
                output.write(package/path,name+'/'+path)
        (feed_dir/(name+'.zip.sha256')).write_text(d.digest(archive)+'  '+archive.name+'\n',encoding='ascii')
        shutil.copy2(package/'portable-manifest.json',feed_dir/(name+'.manifest.json'))
    finally: supervisor.write_bytes(original)
    assets = []
    for idx,suffix in enumerate(('.zip','.zip.sha256','.manifest.json')):
        path = feed_dir/(name+suffix)
        assets.append(dict(id=idx+1,state='uploaded',size=path.stat().st_size,name=path.name,
                           browser_download_url=RELEASES_URL+'/download/v'+version+'/'+path.name))
    d.write(feed_dir/'release.json',dict(id=100+int(version.split('.')[-1]),tag_name='v'+version,
            published_at='2026-11-03T00:00:00Z',draft=False,prerelease=False,assets=assets,body='合成升级演练'))


def run(archive, temp, legacy_launcher=None):
    root, package, feed_dir = temp/'客户 安装', temp/'fixture', temp/'feed'
    feed_dir.mkdir()
    d.write(feed_dir/'release.json', None)
    with zipfile.ZipFile(archive) as source:
        # Only extract our exact locally built package, and only allow manifest-listed paths.
        manifest_name = next(n for n in source.namelist() if n.endswith('/portable-manifest.json'))
        prefix = manifest_name.rsplit('/',1)[0]+'/'
        m = json.loads(source.read(manifest_name))
        for name in [*m['files'],'portable-manifest.json']:
            path = d.confined(package,package/name)
            path.parent.mkdir(parents=True,exist_ok=True)
            with source.open(prefix+name) as src,path.open('xb') as out: shutil.copyfileobj(src,out)
            if name in m['files']: assert d.digest(path) == m['files'][name]
    fixture_files(package,feed_dir)
    manifest(package,'90.0.0')
    shutil.copytree(package,root)
    if legacy_launcher:
        shutil.copy2(legacy_launcher,root/'WeChatAgentBridge.exe')
        previous = manifest(root,'90.0.0')
        previous.pop('launcher_protocol',None)
        d.write(root/'portable-manifest.json',previous)
    release = bootstrap(root)
    env = {**os.environ,'BRIDGE_DISABLE_MONITOR':'1'}
    password = secrets.token_urlsafe(24)

    def rpc(release, **request):
        result = subprocess.run([str(release/'runtime/python.exe'),'-B','-X','utf8','-m','bridge_portable.rpc'],
                    input=json.dumps(request),cwd=release,env=env,encoding='utf-8',capture_output=True,timeout=150,
                    creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode: raise RuntimeError(result.stdout+result.stderr)
        return result.stdout

    def current(): return location(root,d.read(root/'current.json'))
    def until(predicate, seconds=300):
        deadline = time.monotonic()+seconds
        previous = None
        while time.monotonic()<deadline:
            task = d.read(root/'updates/task.json',{})
            if task.get('stage') != previous:
                print('stage:',task.get('stage'),task.get('message'),flush=True)
                previous=task.get('stage')
            if predicate(task): return task
            if task.get('stage') in ('failed','rolled_back','succeeded'):
                raise RuntimeError('Unexpected update result: '+str(task))
            time.sleep(.25)
        raise TimeoutError(str(d.read(root/'updates/task.json')))

    origin = 'http://127.0.0.1:18765'
    def api_install(version):
        with LocalApi(origin,'admin',password) as api:
            state = api.post('updates/check')
            assert state['latest']['version'] == version, state
            target = dict(version=version,release_id=state['latest']['release_id'])
            task = api.post('updates/install',**target)['task']
            assert d.read(root/'updates/task.json')['task_id'] == task['task_id']
    try:
        rpc(release,action='setup',private=True,username='admin',password=password,confirmation=password)
        d.launch_console(release, background=True)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if any((p.info['exe'] or '').lower() == str(release/'WeChatAgentBridge.exe').lower()
                   for p in d.psutil.process_iter(['exe'])): break
            time.sleep(.1)
        else: raise AssertionError('Console did not start before upgrade')
        publish_fixture(package,feed_dir,'90.0.1')
        assert '90.0.1:101' in rpc(release,action='update-check')
        rpc(release,action='update-install',version='90.0.1',release_id=101)
        until(lambda t:t.get('stage')=='succeeded')
        assert current().name.startswith('90.0.1-') and not d.owner(root/'data',current())
        print('Stopped desktop upgrade passed.',flush=True)
        rpc(current(),action='start')
        with closing(sqlite3.connect(root/'data/bridge.sqlite3')) as db, db:
            db.execute('CREATE TABLE acceptance_sent(message TEXT)')
            db.execute("INSERT INTO acceptance_sent VALUES ('confirmed-send-before-update')")
        publish_fixture(package,feed_dir,'90.0.2')
        (feed_dir/'interrupt').touch()
        api_install('90.0.2')
        until(lambda t: not (feed_dir/'interrupt').exists() and current().name.startswith('90.0.2-'))
        # Reopening the stable root after an interrupted worker must resume with
        # the same desktop user's environment (including disabled real monitoring).
        env_before={k:os.environ.get(k) for k in ('BRIDGE_DISABLE_MONITOR',)}
        try:
            os.environ.update({k:env[k] for k in env_before})
            for _ in range(20):
                bootstrap(root)
                if d.read(root/'updates/task.json')['stage']=='succeeded': break
                time.sleep(.5)
            until(lambda t:t.get('stage')=='succeeded')
        finally:
            for key,value in env_before.items():
                if value is None: os.environ.pop(key,None)
                else: os.environ[key]=value
        with LocalApi(origin,'admin',password) as api: assert api.post('updates/query')['current_version']=='90.0.2'
        with closing(sqlite3.connect(root/'data/bridge.sqlite3')) as db, db:
            db.execute("INSERT INTO acceptance_sent VALUES ('confirmed-send-after-restart')")
        print('Authenticated web handoff, interrupted switch and reconnect passed.',flush=True)
        publish_fixture(package,feed_dir,'90.0.3',broken=True)
        api_install('90.0.3')
        until(lambda t:t.get('stage')=='rolled_back')
        with LocalApi(origin,'admin',password) as api: assert api.post('updates/query')['current_version']=='90.0.2'
        with closing(sqlite3.connect(root/'data/bridge.sqlite3')) as db:
            assert db.execute('SELECT message FROM acceptance_sent ORDER BY rowid').fetchall()==[
                ('confirmed-send-before-update',),('confirmed-send-after-restart',)]
        print('Failed candidate rolled back; login, running state and both send records retained.',flush=True)
        service_pid = d.owner(root/'data',current())[0].pid
        d.close_launcher(current())
        with LocalApi(origin,'admin',password) as api: assert api.post('updates/query')['current_version']=='90.0.2'
        assert d.owner(root/'data',current())[0].pid == service_pid
        d.launch_console(current(),background=True)
        rpc(current(),action='stop')
        assert not d.owner(root/'data',current())
        assert any((p.info['exe'] or '').lower() == str(current()/'WeChatAgentBridge.exe').lower()
                   for p in d.psutil.process_iter(['exe']))
        print('Console exit preserves backend; stopped backend leaves console available.',flush=True)
    finally:
        # Only touch a validated supervisor belonging to this disposable installation.
        for child in (root/'releases').iterdir():
            try:
                if d.owner(root/'data',child): d.stop(root/'data',child)
            except ValueError: continue
        for child in (root/'releases').iterdir():
            d.close_launcher(child)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--archive',required=True,type=Path)
    parser.add_argument('--legacy-launcher',type=Path,help='Optional previous native launcher for old-to-new acceptance')
    args=parser.parse_args()
    for port in (8765,18765):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            probe.bind(('127.0.0.1',port))
    with tempfile.TemporaryDirectory(prefix='更新 演练 ',ignore_cleanup_errors=True) as tmp:
        run(args.archive.resolve(),Path(tmp),args.legacy_launcher)
