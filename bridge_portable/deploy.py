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
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler, HTTPSHandler
from urllib.error import HTTPError
import zipfile

import psutil
import certifi

TASK_RUN = 'WeChatBridge-Run'
TASK_DEPLOY = 'WeChatBridge-Deploy'
TASK_CONSOLE = 'WeChatBridge-Console'
# A cold archive migration includes a full backup, integrity check and startup
# check. Keep the standalone controller and service supervisor on one budget.
BACKEND_STARTUP_TIMEOUT_SECONDS = 1800
TERMINAL = {'succeeded', 'failed', 'rolled_back', 'superseded'}
HISTORICAL_DIRECTORIES = frozenset({'migration-backups', 'maintenance-backups'})
CLEANABLE_UPLOAD_STAGES = frozenset({'succeeded', 'rolled_back', 'superseded'})


def checked_path(root, path):
    """Confine maintenance paths without following junctions or symbolic links."""
    root, path = Path(os.path.abspath(root)), Path(os.path.abspath(path))
    if path == root or not path.is_relative_to(root):
        raise ValueError('Maintenance path is outside the installation directory')
    for part in (root, *reversed(path.relative_to(root).parents[:-1])):
        # The root is absolute; the remaining parents are relative to it.
        target = part if part.is_absolute() else root / part
        if target.is_symlink() or (target.exists() and getattr(target.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Maintenance refuses reparse points')
    if path.is_symlink() or (path.exists() and getattr(path.lstat(), 'st_file_attributes', 0) & 0x400):
        raise ValueError('Maintenance refuses reparse points')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Maintenance path escaped the installation directory')
    return path


def data_files(data, *, exclude_history=False):
    """One file inventory for managed backup copying, verification and space checks."""
    data = Path(data)
    pending = [data]
    result = []
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            checked_path(data, path)
            if exclude_history and directory == data and path.name in HISTORICAL_DIRECTORIES:
                if not path.is_dir(): raise ValueError('Historical archive must be a directory')
                continue
            if path.is_dir(): pending.append(path)
            elif path.is_file(): result.append(path)
            else: raise ValueError('Unsupported backup file')
    return result


def file_evidence(root, path):
    path = checked_path(root, path)
    before = path.stat()
    if not path.is_file(): raise ValueError('Expected a regular file')
    checksum = digest(path)
    checked_path(root, path)
    after = path.stat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if identity(before) != identity(after): raise ValueError('File changed while hashing')
    return dict(size_bytes=after.st_size, sha256=checksum)


def historical_references(data):
    data = Path(data)
    files = []
    for name in sorted(HISTORICAL_DIRECTORIES):
        archive = checked_path(data, data / name)
        if not archive.exists(): continue
        for path in data_files(archive):
            files.append(dict(path=path.relative_to(data).as_posix(),
                              source=path.relative_to(data.parent).as_posix(),
                              **file_evidence(data, path)))
    return dict(version=1, files=files)


def http_opener():
    # Server Core/portable Python can lack populated Windows root certificates.
    # Use the pinned runtime's CA bundle while retaining full TLS verification.
    return build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))


def read(path, default=None):
    return json.loads(Path(path).read_text(encoding='utf-8-sig')) if Path(path).is_file() else default


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    from uuid import uuid4
    temp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(10):
            try:
                os.replace(temp, path)
                break
            except PermissionError as exc:
                if attempt == 9 or getattr(exc, 'winerror', None) not in (5, 32, 33): raise
                time.sleep(.025 * (attempt + 1))
    finally:
        temp.unlink(missing_ok=True)


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
                    any(part.rstrip(' .') != part or PureWindowsPath(part).is_reserved() for part in name.parts) or
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


def database_check(data, expected_schema=(9, 10, 11, 12, 13), *, allow_missing=False):
    allowed = (expected_schema,) if isinstance(expected_schema, int) else expected_schema
    for name in ('bridge.sqlite3', 'auth.sqlite3'):
        path = Path(data) / name
        if allow_missing and not path.exists(): continue
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Database integrity check failed')
            if name == 'bridge.sqlite3' and db.execute('PRAGMA user_version').fetchone()[0] not in allowed:
                raise ValueError('Automatic deployment requires the same rollback-compatible database schema')


def compatible_schemas(manifest):
    return manifest.get('database_compatible_schemas', [manifest.get('database_schema')])


def cold_backup(data, backup, *, validate=database_check, exclude_history=False):
    """Publish only a completely copied, verified snapshot; safe to resume."""
    data, backup = Path(data), Path(backup)
    if not backup.is_dir():
        inventory = data_files(data, exclude_history=exclude_history)
        references = historical_references(data) if exclude_history else None
        staging = backup.with_name(backup.name + '.partial')
        if staging.exists(): remove(backup.parent, staging)
        ignore = (lambda directory, names: HISTORICAL_DIRECTORIES.intersection(names)
                  if Path(directory) == data else set()) if exclude_history else None
        shutil.copytree(data, staging, ignore=ignore)
        validate(staging)
        for path in inventory:
            checked_path(data, path)
            target = checked_path(staging, staging / path.relative_to(data))
            if digest(path) != digest(target):
                raise ValueError('Data backup verification failed')
        if references is not None:
            if historical_references(data) != references:
                raise ValueError('Historical archive changed during backup')
            write(checked_path(backup.parent, backup.parent / 'historical-snapshots.json'), references)
        os.replace(staging, backup)
    validate(backup)


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


def launch_console(release, *, background=True):
    release = Path(release).resolve()
    executable = release / 'WeChatAgentBridge.exe'
    # Old launchers have no tray: always leave a visible recovery entry.
    tray = read(release / 'portable-manifest.json', {}).get('launcher_protocol', 0) >= 1
    startup = subprocess.STARTUPINFO() if os.name == 'nt' else None
    if startup is not None:
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0 if background and tray else 1
    return subprocess.Popen([str(executable), *(['--background'] if background and tray else [])],
                            cwd=release, startupinfo=startup, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def notify_launcher_exit(pid):
    """Notify an owned process on this desktop, including invisible top-level windows."""
    import ctypes as c
    from ctypes import wintypes as w
    user = c.WinDLL('user32', use_last_error=True)
    callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    user.EnumWindows.argtypes = [callback_type, w.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
    user.GetPropW.argtypes = [w.HWND, w.LPCWSTR]
    user.GetPropW.restype = w.HANDLE
    user.RegisterWindowMessageW.argtypes = [w.LPCWSTR]
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    message = user.RegisterWindowMessageW('WeChatAgentBridge.ExitForUpdate.v1')
    if not message: raise OSError('Cannot register launcher exit notification')
    @callback_type
    def visit(window, unused):
        owner = w.DWORD()
        user.GetWindowThreadProcessId(window, c.byref(owner))
        if owner.value == pid:
            modern = user.GetPropW(window, 'WeChatAgentBridge.LauncherProtocol')
            # WM_CLOSE remains correct for launchers predating tray support.
            user.PostMessageW(window, message if modern else 0x0010, 0, 0)
        return True
    user.EnumWindows(visit, 0)


def close_launcher(release, timeout=15):
    expected = str(Path(release).resolve() / 'WeChatAgentBridge.exe').lower()
    for process in psutil.process_iter(['pid', 'exe']):
        if (process.info['exe'] or '').lower() == expected:
            deadline = time.monotonic() + timeout
            while process.is_running():
                notify_launcher_exit(process.pid)
                try:
                    process.wait(timeout=min(.25, max(0, deadline - time.monotonic())))
                    break
                except psutil.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Launcher has an operation in progress; no process was terminated') from None


def powershell(script):
    import base64
    script = "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; " + script
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                   check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                   stdout=subprocess.DEVNULL)


def start_task(name, root=None):
    if name not in (TASK_RUN, TASK_DEPLOY, TASK_CONSOLE): raise ValueError('Unknown task')
    if name == TASK_RUN and root:
        current = read(Path(root) / 'current.json')
        data = Path(current.get('data_path') or Path(root) / 'data')
        if owner(data, Path(root) / 'releases' / current['commit']): return
        powershell("$deadline=(Get-Date).AddSeconds(30); while ((Get-ScheduledTask -TaskName 'WeChatBridge-Run').State -eq 'Running') { if ((Get-Date) -gt $deadline) { throw 'Previous run task has not exited' }; Start-Sleep -Milliseconds 250 }")
        write(Path(root) / 'run-permit.json', read(Path(root) / 'current.json'))
    powershell("$ErrorActionPreference='Stop'; Start-ScheduledTask -TaskName '" + name + "'")


def wait_healthy(root, commit, *, active, timeout=BACKEND_STARTUP_TIMEOUT_SECONDS + 120, release=None):
    root = Path(root)
    release = release or root / 'releases' / commit
    managed_version = bool(read(release / 'portable-manifest.json').get('database_schema'))
    config = read(root / 'data/portable-config.json')
    origin = config['origin']
    opener = http_opener()
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
        self.requested_root = Path(os.path.abspath(root))
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

    def cleanup_context(self):
        # A legitimate Windows 8.3 spelling can resolve to a different string.
        # Check actual reparse attributes rather than rejecting path aliases.
        for path in (*reversed(self.requested_root.parents), self.requested_root):
            if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
                raise ValueError('Maintenance refuses a redirected installation root')
        root = self.root
        def metadata(name, default=None):
            return read(checked_path(root, root / name), default)
        for name in ('transaction.json', 'bootstrap-request.json', 'data/deployment-hold.json'):
            if checked_path(root, root / name).exists():
                raise ValueError('Deployment or recovery is in progress: ' + name)
        current = metadata('current.json')
        history = metadata('history.json', [])
        if not isinstance(current, dict) or not re.fullmatch(r'[a-f0-9]{40}', current.get('commit', '')) or current.get('data_path'):
            raise ValueError('Current installation identity is not safe for maintenance')
        if not isinstance(history, list) or not history:
            raise ValueError('Successful deployment history is required for maintenance')
        for entry in history:
            if not isinstance(entry, dict): raise ValueError('Invalid deployment history')
            self.validate_id(entry['request_id'])
            if any(not re.fullmatch(r'[a-f0-9]{40}', entry.get(k, '')) for k in ('commit', 'previous')):
                raise ValueError('Invalid deployment history')
        status_root = checked_path(root, root / 'status')
        for path in status_root.glob('*.json'):
            state = read(checked_path(root, path))
            if not isinstance(state, dict) or state.get('stage') not in TERMINAL | {'adopted'}:
                raise ValueError('A deployment request is still pending: ' + path.name)
        # Distinct successful versions, consistent with complete()/prune().
        versions = {}
        for entry in history:
            versions.pop(entry['commit'], None)
            versions[entry['commit']] = entry
        return dict(current=current, history=list(versions.values())[-3:])

    def cleanup(self, *, apply=False):
        with lock(self.root):
            return self._cleanup(apply=apply)

    def _cleanup(self, *, apply=False):
        """Caller owns deploy.lock; receipts/manifests precede every deletion."""
        root = self.root
        result = dict(stage='preview', candidates=[], protected=[], skipped=[], removed=[],
                      estimated_bytes=0, removed_bytes=0,
                      checked_at=datetime.now(timezone.utc).isoformat())
        try:
            context = self.cleanup_context()
        except (OSError, ValueError, KeyError, TypeError) as error:
            result.update(stage='blocked', reason=str(error))
            return result
        result['current_commit'] = context['current']['commit']
        retained = {entry['request_id'] for entry in context['history']}
        result['retained_requests'] = sorted(retained)

        def protect(path, reason):
            result['protected'].append(dict(path=str(path), reason=reason))

        protect(root / 'data', 'Keep all live data and every original historical snapshot')
        for entry in context['history']:
            identity = entry['request_id']
            backup = root / 'backups' / identity / 'data'
            protect(backup, 'Keep the successful release backup and all nonhistorical data')
            for name in sorted(HISTORICAL_DIRECTORIES):
                directory = backup / name
                try:
                    checked_path(root, directory)
                    if not directory.exists(): continue
                    paths = data_files(directory)
                except (OSError, ValueError) as error:
                    result['skipped'].append(dict(path=str(directory), reason=str(error)))
                    continue
                for path in paths:
                    source = root / 'data' / path.relative_to(backup)
                    try:
                        copied = file_evidence(root, path)
                        original = file_evidence(root, source)
                        if copied != original:
                            protect(path, 'Unique or different historical snapshot')
                            continue
                        result['candidates'].append(dict(kind='historical_duplicate', path=str(path),
                            source=str(source), request_id=identity,
                            relative_path=path.relative_to(backup).as_posix(), **copied))
                    except (OSError, ValueError) as error:
                        result['skipped'].append(dict(path=str(path), reason=str(error)))

        try:
            inbox = checked_path(root, root / 'inbox')
            uploads = sorted(inbox.iterdir())
        except (OSError, ValueError) as error:
            result['skipped'].append(dict(path=str(root / 'inbox'), reason=str(error)))
            uploads = []
        now = datetime.now(timezone.utc)
        for path in uploads:
            if path.suffix != '.zip':
                protect(path, 'Request metadata, unfinished upload or unrecognized file')
                continue
            try:
                checked_path(root, path)
                identity = path.stem
                self.validate_id(identity)
                request = read(checked_path(root, inbox / (identity + '.json')), {})
                state = read(checked_path(root, root / 'status' / (identity + '.json')), {})
                if not isinstance(request, dict) or not isinstance(state, dict):
                    raise ValueError('Invalid upload metadata')
                if identity in retained or request.get('commit') == context['current']['commit']:
                    protect(path, 'Retained or current release upload')
                    continue
                if (not request or request.get('request_id') != identity or state.get('request_id') != identity
                        or state.get('commit') != request.get('commit')
                        or state.get('stage') not in CLEANABLE_UPLOAD_STAGES):
                    protect(path, 'Unknown, failed or unfinished deployment request')
                    continue
                updated = datetime.fromisoformat(state['updated_at'])
                if updated.tzinfo is None or (now - updated).total_seconds() < 86400:
                    protect(path, 'Terminal request is less than 24 hours old')
                    continue
                evidence = file_evidence(root, path)
                if evidence['sha256'] != request.get('sha256'):
                    protect(path, 'Upload checksum differs from the recorded request')
                    continue
                result['candidates'].append(dict(kind='upload', path=str(path), request_id=identity,
                                                 request=request, status=state, **evidence))
            except (OSError, ValueError, KeyError, TypeError) as error:
                result['skipped'].append(dict(path=str(path), reason=str(error)))

        result['estimated_bytes'] = sum(item['size_bytes'] for item in result['candidates'])
        if not apply: return result
        from uuid import uuid4
        receipt = checked_path(root, root / 'maintenance' / 'disk-cleanup' /
                               (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex + '.json'))
        result.update(stage='applying', receipt=str(receipt),
                      free_bytes_before=shutil.disk_usage(root).free)
        write(receipt, result)
        for item in result['candidates']:
            try:
                if self.cleanup_context() != context:
                    raise ValueError('Deployment identity changed during maintenance')
            except (OSError, ValueError, KeyError, TypeError) as error:
                result['skipped'].append(dict(path=item['path'], reason=str(error)))
                result['stage'] = 'interrupted'
                break
            try:
                path = checked_path(root, item['path'])
                expected = {key: item[key] for key in ('size_bytes', 'sha256')}
                if file_evidence(root, path) != expected:
                    raise ValueError('Candidate changed after inventory')
                if item['kind'] == 'historical_duplicate':
                    source = checked_path(root, item['source'])
                    if file_evidence(root, source) != expected:
                        raise ValueError('Historical original changed after inventory')
                    manifest = checked_path(root, root / 'backups' / item['request_id'] / 'historical-snapshots.json')
                    references = read(manifest, dict(version=1, files=[]))
                    if references.get('version') != 1 or not isinstance(references.get('files'), list):
                        raise ValueError('Invalid historical snapshot manifest')
                    reference = dict(path=item['relative_path'], source=source.relative_to(root).as_posix(), **expected)
                    existing = [ref for ref in references['files'] if ref['path'] == reference['path']]
                    if existing and existing != [reference]:
                        raise ValueError('Historical snapshot manifest disagrees with original')
                    if not existing: references['files'].append(reference)
                    write(manifest, references)
                else:
                    identity = item['request_id']
                    for directory, key in (('inbox', 'request'), ('status', 'status')):
                        actual = read(checked_path(root, root / directory / (identity + '.json')))
                        if actual != item[key]: raise ValueError('Upload request changed after inventory')
                checked_path(root, path).unlink()
                result['removed'].append(dict(path=str(path), size_bytes=item['size_bytes'], sha256=item['sha256']))
                result['removed_bytes'] += item['size_bytes']
            except (OSError, ValueError, KeyError, TypeError) as error:
                result['skipped'].append(dict(path=item['path'], reason=str(error)))
            write(checked_path(root, receipt), result)
        if result['stage'] == 'applying': result['stage'] = 'completed'
        result['free_bytes_after'] = shutil.disk_usage(root).free
        write(checked_path(root, receipt), result)
        return result

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
        size = sum(p.stat().st_size for p in data_files(root / 'data', exclude_history=True))
        if shutil.disk_usage(root).free < size + archive.stat().st_size * 4 + 512 * 1024**2:
            raise ValueError('Insufficient space for release and data backup')
        manifest = unpack(archive, destination, request['sha256'], request['commit'])
        if manifest.get('database_schema') not in (9, 10, 11, 12, 13): raise ValueError('Incompatible release database schema')
        database_check(root / 'data', compatible_schemas(manifest))
        current = read(root / 'current.json')
        verify_release(root / 'releases' / current['commit'], current['commit'])
        previous = read(root / 'releases' / current['commit'] / 'portable-manifest.json')
        if manifest['database_schema'] not in compatible_schemas(previous):
            raise ValueError('Install the schema-compatible bridge release before upgrading')
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
                opener = http_opener()
                with opener.open('https://api.github.com/repos/' + repository + '/git/ref/heads/main', timeout=20) as response:
                    if json.load(response)['object']['sha'] != request['commit']:
                        return self.record(request, 'superseded')
            current = self.preflight(request)
            self.record(request, 'stopping', previous=current['commit'])
            close_launcher(root / 'releases' / current['commit'])
            # The deployment lock already excludes console mutations. A busy
            # window must not leave a transaction blocking future local recovery.
            write(root / 'transaction.json', {**request, 'previous': current['commit'], 'activated': False})
            stop(data, root / 'releases' / current['commit'])
            stopped = True
            database_check(data)
            backup = root / 'backups' / request['request_id'] / 'data'
            cold_backup(data, backup, exclude_history=True)
            self.record(request, 'starting', backup=str(backup))
            write(data / 'deployment-hold.json', request)
            write(root / 'current.json', {'commit': request['commit']})
            switched = True
            start_task(TASK_RUN, root)
            wait_healthy(root, request['commit'], active=False)
            candidate = read(root / 'releases' / request['commit'] / 'portable-manifest.json')
            database_check(data, candidate.get('database_output_schemas', [candidate['database_schema']]))
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
                    if switched:
                        close_launcher(root / 'releases' / request['commit'])
                        stop(data, root / 'releases' / request['commit'])
                    # Schema must still be compatible; keep all current data, even before activation.
                    database_check(data)
                    (data / 'deployment-hold.json').unlink(missing_ok=True)
                    write(root / 'current.json', current)
                    start_task(TASK_RUN, root)
                    wait_healthy(root, current['commit'], active=True)
                    (root / 'transaction.json').unlink(missing_ok=True)
                    self.restore_console(current['commit'])
                    return self.record(request, 'rolled_back', error=str(error), activated=activated)
                except Exception as rollback_error:
                    return self.record(request, 'failed', error=str(error), rollback_error=str(rollback_error), activated=activated)
            return self.record(request, 'failed', error=str(error))

    def complete(self, request, current):
        history = read(self.root / 'history.json', [])
        if not any(x['request_id'] == request['request_id'] for x in history):
            history.append(dict(commit=request['commit'], previous=current['commit'], request_id=request['request_id']))
        # A rollback/reapply is another deployment of the same version. Keep
        # three distinct successful versions, each with its latest cold backup.
        versions = {}
        for entry in history:
            versions.pop(entry['commit'], None)
            versions[entry['commit']] = entry
        history = list(versions.values())
        write(self.root / 'history.json', history[-3:])
        (self.root / 'transaction.json').unlink(missing_ok=True)
        result = self.record(request, 'succeeded')
        try:
            self.refresh_controller(request['commit'])
            self.restore_console(request['commit'])
            cleanup = self.prune(history)
            if cleanup['stage'] != 'completed' or cleanup['skipped']:
                result = self.record(request, 'succeeded', cleanup_pending=True)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            result = self.record(request, 'succeeded', cleanup_pending=True)
        return result

    def restore_console(self, commit):
        release = self.root / 'releases' / commit
        if read(release / 'portable-manifest.json', {}).get('launcher_protocol', 0) >= 1:
            expected = str(release / 'WeChatAgentBridge.exe').lower()
            if any((p.info['exe'] or '').lower() == expected for p in psutil.process_iter(['exe'])): return
            quoted = str(release / 'WeChatAgentBridge.exe').replace("'", "''")
            powershell("$expected='" + quoted + "'; $deadline=(Get-Date).AddSeconds(30); "
                       "while ((Get-ScheduledTask -TaskName 'WeChatBridge-Console').State -eq 'Running') { "
                       "if (Get-Process -Name WeChatAgentBridge -ErrorAction SilentlyContinue | Where-Object Path -eq $expected) { exit 0 }; "
                       "if ((Get-Date) -gt $deadline) { throw 'Previous console task has not exited' }; Start-Sleep -Milliseconds 250 }; "
                       "Start-ScheduledTask -TaskName 'WeChatBridge-Console'")
        elif (self.root / 'open-installed.ps1').exists() and os.name == 'nt':
            launch_console(release, background=False)

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
        self.refresh_console(commit)

    def refresh_console(self, commit):
        release = self.root / 'releases' / commit
        if read(release / 'portable-manifest.json', {}).get('launcher_protocol', 0) >= 1:
            # Keep these stable, backwards-compatible entrypoints on rollback.
            for name in ('open-installed.ps1', 'install-tasks.ps1', 'open-console.cmd'):
                source = release / 'deploy' / name
                destination = self.root / name
                temporary = destination.with_suffix('.new')
                shutil.copyfile(source, temporary)
                os.replace(temporary, destination)
            quoted = str(self.root).replace("'", "''")
            powershell("$root='" + quoted + "'; $user=(Get-ScheduledTask -TaskName 'WeChatBridge-Run').Principal.UserId; "
                       "& (Join-Path $root 'install-tasks.ps1') -InstallRoot $root -UserName $user -ConsoleOnly")

    def prune(self, history):
        keep = {x['commit'] for x in history[-3:]} | {history[-1]['previous']}
        for path in (self.root / 'releases').iterdir():
            if path.name not in keep: remove(self.root / 'releases', checked_path(self.root, path))
        backups = self.root / 'backups'
        keep_backups = {x['request_id'] for x in history[-3:]}
        for path in backups.iterdir():
            if path.name not in keep_backups and path.name != 'bootstrap': remove(backups, checked_path(self.root, path))
        return self._cleanup(apply=True)

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
    parser.add_argument('action', choices=['submit', 'apply', 'status', 'preflight', 'rollback', 'bootstrap', 'cleanup'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--request-id')
    parser.add_argument('--commit')
    parser.add_argument('--sha256')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--apply', dest='cleanup_apply', action='store_true', help='Apply the cleanup inventory (cleanup only)')
    args = parser.parse_args()
    if args.cleanup_apply and args.action != 'cleanup': parser.error('--apply is only valid with cleanup')
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
    elif args.action == 'cleanup': result = deployment.cleanup(apply=args.cleanup_apply)
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
