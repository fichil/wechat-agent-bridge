"""Shared local state for authenticated HTTP and desktop requests."""
from pathlib import Path
import threading
import time
from uuid import uuid4

from .deploy import read, write, lock, owner
from . import update_feed as feed

TERMINAL = {'succeeded', 'failed', 'rolled_back'}


def installation(root):
    root = Path(root).resolve()
    if root.parent.name == 'releases' and (root.parent.parent / 'installation.json').is_file():
        return root.parent.parent
    if (root / 'installation.json').is_file(): return root
    return None


class Updates:
    def __init__(self, root=None, data=None):
        from bridge_paths import ROOT, DATA
        self.release = Path(root or ROOT).resolve()
        self.data = Path(data or DATA).resolve()
        self.root = installation(self.release)
        info = read(self.root / 'installation.json', {}) if self.root else {}
        self.manifest = read(self.release / 'portable-manifest.json', {})
        self.mode = ('portable' if info.get('mode') == 'portable' else 'managed') if self.root else 'source'
        self.directory = self.root / 'updates' if self.mode == 'portable' else self.data / 'updates'

    def query(self):
        state = read(self.directory / 'feed.json', {}) if self.mode == 'portable' else {}
        task = read(self.directory / 'task.json') if self.mode == 'portable' else None
        latest = state.get('latest')
        if latest and self.manifest and feed.version_key(latest['version']) <= feed.version_key(self.manifest['version']): latest = None
        return dict(mode=self.mode, current_version=self.manifest.get('version', 'source'),
                    current_commit=self.manifest.get('commit', 'source'), releases_url=feed.RELEASES_URL,
                    can_install=self.mode == 'portable', checked_at=state.get('checked_at'),
                    checking=bool(state.get('checking') and time.time() - state.get('started_at', 0) < 120),
                    error=state.get('error'), latest=latest, task=task)

    def check(self, force=False):
        if self.mode != 'portable': return self.query()
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            with lock(self.directory):
                old = read(self.directory / 'feed.json', {})
                if not force and time.time() - old.get('checked_at', 0) < feed.CHECK_INTERVAL: return self.query()
                if old.get('checking') and time.time() - old.get('started_at', 0) < 120: return self.query()
                write(self.directory / 'feed.json', {**old, 'checking': True, 'started_at': time.time()})
        except OSError:
            return self.query()
        try:
            value = feed.latest(self.manifest['version'])
            state = dict(checked_at=time.time(), latest=value, error=None, checking=False)
        except Exception as exc:
            message = str(exc) if isinstance(exc, ValueError) else '无法连接 GitHub，请检查网络后重试。'
            state = dict(checked_at=time.time(), latest=old.get('latest'), error=message, checking=False)
        # We own the in-flight check. Atomic replacement does not contend with
        # a long-running launcher management operation.
        write(self.directory / 'feed.json', state)
        return self.query()

    def install(self, version, release_id, *, from_web=False):
        if self.mode != 'portable': raise ValueError('此安装由源码或服务器部署流程管理，不支持客户端升级。')
        self.directory.mkdir(parents=True, exist_ok=True)
        with lock(self.directory):
            current = read(self.root / 'current.json')
            if current['directory'] != self.release.name: raise ValueError('程序版本已切换，请重新打开当前版本。')
            task = read(self.directory / 'task.json')
            if task and task['stage'] not in TERMINAL:
                if task['version'] != version: raise ValueError('另一个更新任务正在进行，请等待完成。')
                return self.query()
            if (self.directory / 'transaction.json').exists(): raise ValueError('上次更新尚未恢复，请重新打开启动器。')
            candidate = read(self.directory / 'feed.json', {}).get('latest')
            if not candidate or (candidate['version'], candidate['release_id']) != (version, release_id) or not candidate['ready']:
                raise ValueError('目标版本已变化或附件未就绪，请重新检查更新。')
            if feed.version_key(version) <= feed.version_key(self.manifest['version']): raise ValueError('只能升级到更新的正式版本。')
            task_id = uuid4().hex
            running = bool(owner(self.data, self.release))
            if from_web and not running: raise ValueError('当前后台没有受支持的启动器，请从安装根目录启动后升级。')
            request = dict(task_id=task_id, target=candidate, previous=current, running=running)
            write(self.directory / 'request.json', request)
            write(self.directory / 'task.json', dict(task_id=task_id, version=version, stage='queued', message='等待更新控制器', updated_at=time.time()))
        # Running services launch workers from their supervisor, outside the
        # backend's kill-on-close Job. A stopped desktop launch can do so directly.
        if not request['running']:
            from .update_engine import spawn_worker
            spawn_worker(self.root)
        return self.query()


class AutomaticChecks:
    def __init__(self):
        self.stop = threading.Event()
        self.updates = Updates()
        self.thread = None
        if self.updates.mode == 'portable':
            self.thread = threading.Thread(target=self.loop, name='release-check', daemon=True)
            self.thread.start()

    def loop(self):
        while not self.stop.is_set():
            try: self.updates.check()
            except Exception: pass
            self.stop.wait(60)

    def close(self):
        self.stop.set()
        if self.thread: self.thread.join(timeout=2)
