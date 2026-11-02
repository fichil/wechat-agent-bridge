"""Local deployment controller. SSH submits files; interactive tasks own processes.

This module intentionally uses no application imports, credentials or public API.
Its stable copy and runtime live outside the release being replaced.
"""
import argparse
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
import zipfile

import psutil

TASK_RUN = 'WeChatBridge-Run'
TASK_DEPLOY = 'WeChatBridge-Deploy'
TERMINAL = {'succeeded', 'failed', 'rolled_back', 'superseded'}


def read(path, default=None):
    return json.loads(Path(path).read_text(encoding='utf-8-sig')) if Path(path).is_file() else default


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=True, indent=2), encoding='utf-8')
    os.replace(temp, path)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def confined(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError('Path is outside the installation directory')
    return path


def remove(root, path):
    path = confined(root, path)
    if path.is_dir(): shutil.rmtree(path)
    elif path.exists(): path.unlink()


def verify_release(path, expected_commit=None):
    path = Path(path).resolve()
    manifest = read(path / 'portable-manifest.json')
    if not manifest or manifest.get('development') or not re.fullmatch(r'[a-f0-9]{40}', manifest.get('commit', '')):
        raise ValueError('An exact, non-development release is required')
    if expected_commit and manifest['commit'] != expected_commit:
        raise ValueError('Release commit differs from the requested commit')
    for name, expected in manifest['files'].items():
        target = confined(path, path / name)
        if not target.is_file() or digest(target) != expected:
            raise ValueError('Release file checksum mismatch: ' + name)
    return manifest


def unpack(archive, destination, expected_hash, commit):
    archive, destination = Path(archive), Path(destination)
    if digest(archive) != expected_hash:
        raise ValueError('Archive checksum mismatch')
    if destination.exists():
        return verify_release(destination, commit)
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if not entries or sum(i.file_size for i in entries) > 2 * 1024**3:
            raise ValueError('Invalid package size')
        roots = set()
        normalized = []
        for entry in entries:
            name = PurePosixPath(entry.filename)
            if ('\\' in entry.filename or ':' in entry.filename or name.is_absolute() or
                    '..' in name.parts or len(name.parts) < 2 or entry.is_dir() or
                    (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('Invalid package path')
            roots.add(name.parts[0])
            relative = '/'.join(name.parts[1:])
            if relative.split('/')[0].lower() in ('data', '.local', '.git'):
                raise ValueError('Runtime data cannot be deployed')
            normalized.append((entry, relative))
        if len(roots) != 1 or len({n.lower() for _, n in normalized}) != len(normalized):
            raise ValueError('Duplicate or mixed package roots')
        manifest_name = next((e.filename for e, n in normalized if n == 'portable-manifest.json'), None)
        manifest = json.loads(source.read(manifest_name)) if manifest_name else {}
        if {n for _, n in normalized} != set(manifest.get('files', {})) | {'portable-manifest.json'}:
            raise ValueError('Package does not match its allowlist')
        destination.mkdir(parents=True)
        try:
            for entry, name in normalized:
                target = confined(destination, destination / name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as incoming, target.open('xb') as output:
                    shutil.copyfileobj(incoming, output)
            return verify_release(destination, commit)
        except BaseException:
            remove(destination.parent, destination)
            raise


def database_check(data, expected_schema=9):
    for name in ('bridge.sqlite3', 'auth.sqlite3'):
        path = Path(data) / name
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Database integrity check failed')
            if name == 'bridge.sqlite3' and db.execute('PRAGMA user_version').fetchone()[0] != expected_schema:
                raise ValueError('Automatic deployment requires the same rollback-compatible database schema')


def owner(data, release):
    value = read(Path(data) / 'production/current.json')
    if not value: return None
    try:
        process = psutil.Process(value['pid'])
        if abs(process.create_time() - value['created']) > .01: return None
        script = str(Path(release) / 'scripts/production_server.py').lower()
        if script not in [arg.lower() for arg in process.cmdline()]:
            raise ValueError('Running process belongs to a different release')
        return process, value
    except psutil.NoSuchProcess:
        return None


def stop(data, release, timeout=2040):
    current = owner(data, release)
    if not current: return
    process, value = current
    marker = confined(Path(data) / 'production', value['stop_file'])
    if marker.name != 'stop.request': raise ValueError('Invalid stop marker')
    marker.write_text('stop\n', encoding='utf-8')
    process.wait(timeout)


def close_launcher(release):
    expected = str(Path(release) / 'WeChatAgentBridge.exe').lower()
    for process in psutil.process_iter(['pid', 'exe']):
        if (process.info['exe'] or '').lower() == expected:
            powershell("$p=Get-Process -Id " + str(process.pid) + "; $null=$p.CloseMainWindow(); if (-not $p.WaitForExit(15000)) { throw 'Launcher has an operation in progress' }")


def powershell(script):
    import base64
    script = "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; " + script
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                   check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                   stdout=subprocess.DEVNULL)


def start_task(name, root=None):
    if name not in (TASK_RUN, TASK_DEPLOY): raise ValueError('Unknown task')
    if name == TASK_RUN and root:
        current = read(Path(root) / 'current.json')
        data = Path(current.get('data_path') or Path(root) / 'data')
        if owner(data, Path(root) / 'releases' / current['commit']): return
        powershell("$deadline=(Get-Date).AddSeconds(30); while ((Get-ScheduledTask -TaskName 'WeChatBridge-Run').State -eq 'Running') { if ((Get-Date) -gt $deadline) { throw 'Previous run task has not exited' }; Start-Sleep -Milliseconds 250 }")
        write(Path(root) / 'run-permit.json', read(Path(root) / 'current.json'))
    powershell("$ErrorActionPreference='Stop'; Start-ScheduledTask -TaskName '" + name + "'")


def wait_healthy(root, commit, *, active, timeout=240):
    root = Path(root)
    release = root / 'releases' / commit
    managed_version = bool(read(release / 'portable-manifest.json').get('database_schema'))
    config = read(root / 'data/portable-config.json')
    origin = config['origin']
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            process = owner(root / 'data', release)
            business = read(root / 'data/production/business.json', {})
            if not process or (managed_version and (business.get('commit') != commit or business.get('active') is not active)):
                raise ValueError('Waiting for candidate activation state')
            # Verify status belongs to this supervisor's current backend, not a stale file.
            children = {p.pid for p in process[0].children(recursive=True)}
            if managed_version and business.get('pid') not in children: raise ValueError('Stale business status')
            with opener.open(origin + '/api/v1/health', timeout=3) as response:
                if json.load(response).get('service') != 'wechat-agent-bridge': raise ValueError('Wrong service')
            with opener.open(origin + '/login', timeout=3) as response:
                if b'<html' not in response.read().lower(): raise ValueError('Missing frontend')
            request = Request(origin + '/api/v1/accounts/query', data=b'{}',
                              headers={'Origin': origin, 'Content-Type': 'application/json'})
            try:
                opener.open(request, timeout=3)
                raise ValueError('Business API allowed anonymous access')
            except HTTPError as error:
                if error.code != 401: raise
            return
        except (OSError, ValueError, KeyError, psutil.Error):
            time.sleep(1)
    raise TimeoutError('Candidate did not reach the required healthy state')


@contextmanager
def lock(root):
    import msvcrt
    path = Path(root) / 'deploy.lock'
    with path.open('a+b') as stream:
        if stream.tell() == 0: stream.write(b'0'); stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        try: yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


class Deployment:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if not (self.root / 'installation.json').is_file(): raise ValueError('Installation is not configured')

    def status(self, request_id):
        self.validate_id(request_id)
        return read(self.root / 'status' / (request_id + '.json'), {'request_id': request_id, 'stage': 'missing'})

    @staticmethod
    def validate_id(value):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,96}', value): raise ValueError('Invalid request identifier')

    def record(self, request, stage, **extra):
        value = {**self.status(request['request_id']), **extra, 'request_id': request['request_id'],
                 'commit': request['commit'], 'stage': stage, 'updated_at': datetime.now(timezone.utc).isoformat()}
        write(self.root / 'status' / (request['request_id'] + '.json'), value)
        return value

    def submit(self, request_id, commit, checksum):
        self.validate_id(request_id)
        if not re.fullmatch(r'[a-f0-9]{40}', commit) or not re.fullmatch(r'[a-f0-9]{64}', checksum):
            raise ValueError('Invalid commit or checksum')
        request = dict(request_id=request_id, commit=commit, sha256=checksum)
        existing = read(self.root / 'inbox' / (request_id + '.json'))
        if existing:
            if any(existing[k] != request[k] for k in request): raise ValueError('Conflicting request identifier')
            if self.status(request_id)['stage'] not in TERMINAL: start_task(TASK_DEPLOY)
            return self.status(request_id)
        archive = self.root / 'inbox' / (request_id + '.zip')
        incoming = archive.with_suffix('.zip.part')
        if digest(incoming) != checksum: raise ValueError('Incomplete upload')
        os.replace(incoming, archive)
        request['created_at'] = datetime.now(timezone.utc).isoformat()
        self.record(request, 'queued')
        write(self.root / 'inbox' / (request_id + '.json'), request)
        start_task(TASK_DEPLOY)
        return self.status(request_id)

    def preflight(self, request):
        root = self.root
        destination = root / 'releases' / request['commit']
        archive = root / 'inbox' / (request['request_id'] + '.zip')
        size = sum(p.stat().st_size for p in (root / 'data').rglob('*') if p.is_file())
        if shutil.disk_usage(root).free < size + archive.stat().st_size * 4 + 512 * 1024**2:
            raise ValueError('Insufficient space for release and data backup')
        manifest = unpack(archive, destination, request['sha256'], request['commit'])
        if manifest.get('database_schema') != 9: raise ValueError('Incompatible release database schema')
        database_check(root / 'data', manifest['database_schema'])
        current = read(root / 'current.json')
        verify_release(root / 'releases' / current['commit'], current['commit'])
        return current

    def apply(self, request):
        root, data = self.root, self.root / 'data'
        current = None
        stopped = switched = activated = False
        try:
            if read(root / 'current.json')['commit'] == request['commit']:
                transaction = read(root / 'transaction.json', {})
                if transaction.get('request_id') == request['request_id']:
                    current = {'commit': transaction['previous']}
                    stopped = switched = True
                    start_task(TASK_RUN, root)
                    if (data / 'deployment-hold.json').exists():
                        wait_healthy(root, request['commit'], active=False)
                        write(root / 'transaction.json', {**transaction, 'activated': True})
                        (data / 'deployment-hold.json').unlink()
                    activated = True
                    wait_healthy(root, request['commit'], active=True)
                    return self.complete(request, current)
                wait_healthy(root, request['commit'], active=True)
                return self.record(request, 'succeeded', unchanged=True)
            self.record(request, 'preflight')
            repository = read(root / 'installation.json').get('repository')
            if repository:
                if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository): raise ValueError('Invalid repository')
                opener = build_opener(ProxyHandler({}))
                with opener.open('https://api.github.com/repos/' + repository + '/git/ref/heads/main', timeout=20) as response:
                    if json.load(response)['object']['sha'] != request['commit']:
                        return self.record(request, 'superseded')
            current = self.preflight(request)
            write(root / 'transaction.json', {**request, 'previous': current['commit'], 'activated': False})
            self.record(request, 'stopping', previous=current['commit'])
            close_launcher(root / 'releases' / current['commit'])
            stop(data, root / 'releases' / current['commit'])
            stopped = True
            database_check(data)
            backup = root / 'backups' / request['request_id'] / 'data'
            if backup.exists():
                # A previous attempt stopped before switching; never overwrite its cold snapshot.
                database_check(backup)
            else:
                shutil.copytree(data, backup)
            database_check(backup)
            for path in data.rglob('*'):
                if path.is_file() and digest(path) != digest(backup / path.relative_to(data)):
                    raise ValueError('Data backup verification failed')
            self.record(request, 'starting', backup=str(backup))
            write(data / 'deployment-hold.json', request)
            write(root / 'current.json', {'commit': request['commit']})
            switched = True
            start_task(TASK_RUN, root)
            wait_healthy(root, request['commit'], active=False)
            database_check(data)
            self.record(request, 'activating')
            # From this point onward old snapshots must never replace live data.
            activated = True
            write(root / 'transaction.json', {**request, 'previous': current['commit'], 'activated': True})
            (data / 'deployment-hold.json').unlink()
            wait_healthy(root, request['commit'], active=True, timeout=45)
            return self.complete(request, current)
        except Exception as error:
            if stopped and current:
                try:
                    if switched: stop(data, root / 'releases' / request['commit'])
                    # Schema must still be compatible; keep all current data, even before activation.
                    database_check(data)
                    (data / 'deployment-hold.json').unlink(missing_ok=True)
                    write(root / 'current.json', current)
                    start_task(TASK_RUN, root)
                    wait_healthy(root, current['commit'], active=True)
                    (root / 'transaction.json').unlink(missing_ok=True)
                    return self.record(request, 'rolled_back', error=str(error), activated=activated)
                except Exception as rollback_error:
                    return self.record(request, 'failed', error=str(error), rollback_error=str(rollback_error), activated=activated)
            return self.record(request, 'failed', error=str(error))

    def complete(self, request, current):
        history = read(self.root / 'history.json', [])
        if not any(x['request_id'] == request['request_id'] for x in history):
            history.append(dict(commit=request['commit'], previous=current['commit'], request_id=request['request_id']))
        write(self.root / 'history.json', history[-3:])
        (self.root / 'transaction.json').unlink(missing_ok=True)
        result = self.record(request, 'succeeded')
        try:
            self.refresh_controller(request['commit'])
            self.prune(history)
        except OSError: self.record(request, 'succeeded', cleanup_pending=True)
        return result

    def refresh_controller(self, commit):
        # The running interpreter has loaded this module already. Replace its
        # stable entrypoint last so the next request uses the verified release.
        release = self.root / 'releases' / commit
        for source, destination in (
            ('deploy/run-installed.ps1', self.root / 'run-installed.ps1'),
            ('deploy/deploy-task.ps1', self.root / 'deploy-task.ps1'),
            ('bridge_portable/deploy.py', self.root / 'controller/deploy.py'),
        ):
            if not (release / source).is_file(): continue  # Older portable releases.
            destination.parent.mkdir(exist_ok=True)
            temporary = destination.with_suffix('.new')
            shutil.copyfile(release / source, temporary)
            os.replace(temporary, destination)

    def prune(self, history):
        keep = {x['commit'] for x in history[-3:]} | {history[-1]['previous']}
        for path in (self.root / 'releases').iterdir():
            if path.name not in keep: remove(self.root / 'releases', path)
        backups = self.root / 'backups'
        keep_backups = {x['request_id'] for x in history[-3:]}
        for path in backups.iterdir():
            if path.name not in keep_backups and path.name != 'bootstrap': remove(backups, path)

    def pending(self):
        with lock(self.root):
            adoption = read(self.root / 'bootstrap-request.json')
            if adoption:
                write(self.root / 'status/bootstrap.json', {'stage': 'adopting'})
                try:
                    result = bootstrap(self.root, adoption['source'])
                    (self.root / 'bootstrap-request.json').unlink()
                except Exception as error:
                    result = dict(stage='failed', error=str(error))
                write(self.root / 'status/bootstrap.json', result)
                return result
            result = {'stage': 'idle'}
            while True:
                requests = [read(p) for p in (self.root / 'inbox').glob('*.json')]
                requests = sorted((r for r in requests if self.status(r['request_id'])['stage'] not in TERMINAL),
                                  key=lambda r: r['created_at'])
                if not requests: return result
                transaction = read(self.root / 'transaction.json', {})
                interrupted = next((r for r in requests if r['request_id'] == transaction.get('request_id')), None)
                selected = interrupted or requests[-1]
                if not interrupted:
                    for request in requests[:-1]: self.record(request, 'superseded')
                result = self.apply(selected)


def bootstrap(root, source):
    """One-time adoption; source data remains intact as the original recovery copy."""
    root, source = Path(root).resolve(), Path(source).resolve()
    if root == source or root.is_relative_to(source) or source.is_relative_to(root):
        raise ValueError('Installation and source must be separate directories')
    manifest = verify_release(source)
    target = root / 'releases' / manifest['commit']
    if (root / 'data').exists():
        if read(root / 'current.json') != {'commit': manifest['commit']}:
            raise ValueError('Bootstrap recovery does not match the original release')
        verify_release(target)
        database_check(root / 'data')
        start_task(TASK_RUN, root)
        wait_healthy(root, manifest['commit'], active=True)
        return dict(stage='adopted', commit=manifest['commit'])
    target.mkdir(parents=True, exist_ok=True)
    for name in [*manifest['files'], 'portable-manifest.json']:
        output = confined(target, target / name)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, output)
    verify_release(target)
    close_launcher(source)
    stop(source / 'data', source)
    try:
        database_check(source / 'data')
        staging = root / 'bootstrap-data'
        if staging.exists(): remove(root, staging)
        shutil.copytree(source / 'data', staging)
        database_check(staging)
        for path in (source / 'data').rglob('*'):
            if path.is_file() and digest(path) != digest(staging / path.relative_to(source / 'data')):
                raise ValueError('Bootstrap data checksum mismatch')
        os.replace(staging, root / 'data')
        write(root / 'current.json', {'commit': manifest['commit']})
        for name in ('inbox', 'status', 'backups'): (root / name).mkdir(exist_ok=True)
        start_task(TASK_RUN, root)
        wait_healthy(root, manifest['commit'], active=True)
        return dict(stage='adopted', commit=manifest['commit'])
    except Exception:
        # Run the original bits with the intact source data if adoption failed.
        write(root / 'current.json', {'commit': manifest['commit'], 'data_path': str(source / 'data')})
        start_task(TASK_RUN, root)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['submit', 'apply', 'status', 'preflight', 'rollback', 'bootstrap'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--request-id')
    parser.add_argument('--commit')
    parser.add_argument('--sha256')
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    deployment = Deployment(args.root)
    if args.action == 'bootstrap':
        if (args.root / 'data').exists(): raise ValueError('Installation data already exists')
        write(args.root / 'bootstrap-request.json', {'source': str(args.source.resolve())})
        write(args.root / 'status/bootstrap.json', {'stage': 'queued'})
        start_task(TASK_DEPLOY)
        result = {'stage': 'queued', 'request_id': 'bootstrap'}
    elif args.action == 'submit': result = deployment.submit(args.request_id, args.commit, args.sha256)
    elif args.action == 'status': result = deployment.status(args.request_id) if args.request_id else read(args.root / 'current.json')
    elif args.action == 'apply': result = deployment.pending()
    elif args.action == 'preflight': result = deployment.preflight(read(args.root / 'inbox' / (args.request_id + '.json')))
    else:
        with lock(args.root):
            history = read(args.root / 'history.json', [])
            if not history: raise ValueError('No previous successful deployment')
            previous = history[-1]['previous']
            current = read(args.root / 'current.json')['commit']
            verify_release(args.root / 'releases' / previous, previous)
            database_check(args.root / 'data')
            stop(args.root / 'data', args.root / 'releases' / current)
            write(args.root / 'current.json', {'commit': previous})
            start_task(TASK_RUN, args.root)
            wait_healthy(args.root, previous, active=True)
            result = dict(stage='rolled_back', commit=previous)
    print(json.dumps(result))


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print(json.dumps(dict(stage='failed', error=str(error))))
        sys.exit(1)
