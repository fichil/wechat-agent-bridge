"""Durable, side-by-side updates for standalone Windows installations."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import psutil

from . import deploy as d
from . import update_feed as feed
from .updates import TERMINAL

HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
POINTER = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?-[0-9a-f]{40}')


def location(root, pointer):
    name = pointer['directory']
    if not POINTER.fullmatch(name) or name != pointer['version'] + '-' + pointer['commit']:
        raise ValueError('程序版本指针无效。')
    return d.confined(root / 'releases', root / 'releases' / name)


def pointer(manifest):
    if manifest.get('development') or manifest.get('architecture') != 'windows-x64':
        raise ValueError('只接受 Windows x64 正式构建。')
    feed.version_key(manifest['version'])
    return dict(directory=manifest['version'] + '-' + manifest['commit'], version=manifest['version'], commit=manifest['commit'])


def controller(root, release):
    # Private runtime outside the live backend, retained with its corresponding release.
    parent = root / 'controller'
    parent.mkdir(exist_ok=True)
    result = d.confined(parent, parent / release.name)
    if result.is_dir(): return result
    stage = result.with_suffix('.preparing')
    if stage.exists(): d.remove(parent, stage)
    stage.mkdir()
    for name in ('runtime', 'bridge_portable'):
        shutil.copytree(release / name, stage / name)
    shutil.copy2(release / 'bridge_paths.py', stage / 'bridge_paths.py')
    os.replace(stage, result)
    return result


def bootstrap(root):
    root = Path(root).resolve()
    (root / 'updates').mkdir(exist_ok=True)
    with d.lock(root / 'updates'):
        current = d.read(root / 'current.json')
        if not current:
            manifest = d.verify_release(root)
            current = pointer(manifest)
            release = location(root, current)
            stage = release.with_name('.bootstrap')
            (root / 'releases').mkdir(exist_ok=True)
            if not release.exists():
                if stage.exists(): d.remove(root / 'releases', stage)
                stage.mkdir()
                for name in [*manifest['files'], 'portable-manifest.json']:
                    target = d.confined(stage, stage / name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(root / name, target)
                d.verify_release(stage, current['commit'])
                os.replace(stage, release)
            d.verify_release(release, current['commit'])
            controller(root, release)
            (root / 'data').mkdir(exist_ok=True)
            d.write(root / 'installation.json', dict(version=1, mode='portable', repository=feed.REPOSITORY))
            d.write(root / 'current.json', current)
    if d.read(root / 'installation.json', {}).get('mode') != 'portable':
        raise ValueError('此目录由服务器部署流程管理。')
    spawn_if_pending(root, recover=True)
    return location(root, d.read(root / 'current.json'))


def spawn_worker(root):
    req = d.read(root / 'updates/request.json')
    path = root / 'controller' / location(root, req['previous']).name
    return subprocess.Popen([str(path / 'runtime/python.exe'), '-B', '-X', 'utf8', '-m',
                             'bridge_portable.update_engine', str(root)], cwd=path, creationflags=HIDDEN,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def spawn_if_pending(root, *, recover=False):
    task = d.read(root / 'updates/task.json', {})
    if not task or (task['stage'] in TERMINAL and not (recover and (root / 'updates/transaction.json').exists())): return
    # Each supervisor/bootstrap may wake an interrupted request; only one worker applies it.
    worker = d.read(root / 'updates/worker.json', {})
    if worker:
        try:
            proc = psutil.Process(worker['pid'])
            if abs(proc.create_time() - worker['created']) < .01: return
        except psutil.NoSuchProcess: pass
    spawn_worker(root)


def start_release(root, release):
    if d.owner(root / 'data', release): return
    env = {**os.environ, 'BRIDGE_DATA_DIR': str(root / 'data')}
    result = subprocess.run([str(release / 'runtime/python.exe'), '-B', '-X', 'utf8', '-c',
                            'from bridge_portable.control import start; print(start())'],
                           cwd=release, env=env, capture_output=True, encoding='utf-8', errors='replace', creationflags=HIDDEN)
    if result.returncode and not d.owner(root / 'data', release):
        raise ValueError('后台启动失败，请查看 data/production 日志。')


def drain(data, release, task_id, timeout=2010):
    current = d.owner(data, release)
    if not current: return
    marker = data / 'update-drain.json'
    d.write(marker, dict(task_id=task_id))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ack = d.read(data / 'production/update-drained.json', {})
        if ack.get('task_id') == task_id and ack.get('pid') in {p.pid for p in current[0].children(recursive=True)}: return
        if not d.owner(data, release): return
        time.sleep(.5)
    marker.unlink(missing_ok=True)
    raise ValueError('等待在途 AI 或发送任务结束超时，已取消升级。')


def check_data(data, schemas=(9, 10, 11, 12)):
    d.database_check(data, schemas, allow_missing=True)


class Engine:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if d.read(self.root / 'installation.json', {}).get('mode') != 'portable':
            raise ValueError('此安装不支持客户端升级。')
        self.state = self.root / 'updates'
        self.data = self.root / 'data'

    def record(self, stage, message, **extra):
        d.write(self.state / 'task.json', dict(task_id=self.req['task_id'], version=self.req['target']['version'],
                stage=stage, message=message, updated_at=time.time(), **extra))

    def candidate(self):
        target = self.req['target']
        version = target['version']
        # Re-read the release at installation time. A withdrawn release or replaced
        # attachment is not the target the customer confirmed.
        fresh = feed.select_release([json.loads(feed.fetch(feed.API_URL + '/tags/' + target['tag']))], self.old['version'])
        if not fresh or not fresh['ready'] or (fresh['release_id'], fresh['assets']) != (target['release_id'], target['assets']):
            raise ValueError('发布内容已变化，请重新检查更新。')
        name = 'WeChatAgentBridge-' + version + '-windows-x64'
        archive = name + '.zip'
        assets = fresh['assets']
        sums = feed.fetch(assets[archive + '.sha256']['url'], limit=1024).decode('ascii').split()
        if len(sums) != 2 or sums[1] != archive or not re.fullmatch('[a-f0-9]{64}', sums[0]):
            raise ValueError('发布校验文件无效。')
        asset_digest = assets[archive].get('digest')
        if asset_digest is not None and asset_digest != 'sha256:' + sums[0]:
            raise ValueError('GitHub 附件摘要与校验文件不一致。')
        manifest = json.loads(feed.fetch(assets[name + '.manifest.json']['url'], limit=8*1024**2))
        new = pointer(manifest)
        if new['version'] != version: raise ValueError('更新清单版本与 Release 不一致。')
        dest = location(self.root, new)
        size = assets[archive]['size']
        data_size = sum(p.stat().st_size for p in self.data.rglob('*') if p.is_file())
        if shutil.disk_usage(self.root).free < data_size + size * 6 + 512 * 1024**2:
            raise ValueError('磁盘空间不足，无法暂存程序并备份数据。')
        tmp = self.state / (self.req['task_id'] + '.part')
        stage = dest.with_name('.stage-' + self.req['task_id'])
        try:
            last = -1
            def progress(count, _):
                nonlocal last
                now = int(time.monotonic())
                if now != last:
                    self.record('downloading', '正在下载更新包', downloaded=count, total=size)
                    last = now
            with tmp.open('wb') as stream:
                feed.fetch(assets[archive]['url'], output=stream, limit=size, progress=progress)
            if tmp.stat().st_size != size: raise ValueError('更新包下载不完整。')
            self.record('verifying', '正在校验更新包')
            if stage.exists(): d.remove(stage.parent, stage)
            actual = d.unpack(tmp, stage, sums[0], new['commit'])
            if actual != manifest: raise ValueError('包内外清单不一致。')
            if not dest.exists(): os.replace(stage, dest)
            if d.verify_release(dest, new['commit']) != manifest: raise ValueError('已有版本目录与更新包不一致。')
            listed = set(manifest['files']) | {'portable-manifest.json'}
            if any(p.relative_to(dest).as_posix() not in listed for p in dest.rglob('*') if p.is_file()):
                raise ValueError('目标版本目录包含清单以外的文件。')
            # Prove the candidate interpreter and application load even when the
            # customer's backend is unconfigured or intentionally left stopped.
            smoke = subprocess.run([str(dest/'runtime/python.exe'), '-B', '-c',
                'from backend.app.main import create_app; from wechat_sender.compatibility import build_identity; build_identity(); create_app().openapi()'],
                cwd=dest, capture_output=True, timeout=90, creationflags=HIDDEN)
            if smoke.returncode: raise ValueError('候选程序离线自检失败。')
            controller(self.root, dest)
        finally:
            tmp.unlink(missing_ok=True)
            if stage.exists(): d.remove(stage.parent, stage)
        return new, manifest

    def start_healthy(self, point, active):
        release = location(self.root, point)
        start_release(self.root, release)
        d.wait_healthy(self.root, point['commit'], active=active, release=release)

    def rollback(self, journal, cause):
        previous = self.old
        active = d.read(self.root / 'current.json')
        if active != previous:
            d.stop(self.data, location(self.root, active))
        check_data(self.data, d.compatible_schemas(d.read(location(self.root, previous) / 'portable-manifest.json')))
        (self.data / 'deployment-hold.json').unlink(missing_ok=True)
        (self.data / 'update-drain.json').unlink(missing_ok=True)
        d.write(self.root / 'current.json', previous)
        if self.req['running']: self.start_healthy(previous, True)
        self.record('rolled_back', '已恢复原版本并保留最新数据：' + cause)

    def apply(self):
        self.req = d.read(self.state / 'request.json')
        self.old = self.req['previous']
        if not re.fullmatch('[0-9a-f]{32}', self.req['task_id']): raise ValueError('更新任务编号无效。')
        journal_path = self.state / 'transaction.json'
        journal = d.read(journal_path)
        if journal and journal['task_id'] != self.req['task_id']: raise ValueError('上一笔更新尚未恢复，请先恢复原任务。')
        previous = location(self.root, self.old)
        reopen = False
        try:
            if not journal:
                self.record('verifying', '正在确认发布附件与可用空间')
                if d.read(self.root / 'current.json') != self.old: raise ValueError('当前版本已变化，请重新检查。')
                old_manifest = d.verify_release(previous, self.old['commit'])
                new, candidate = self.candidate()
                check_data(self.data, d.compatible_schemas(candidate))
                if not set(candidate.get('database_output_schemas', [candidate['database_schema']])).issubset(d.compatible_schemas(old_manifest)):
                    raise ValueError('当前版本不能读取升级后的数据库，请先安装兼容桥接版本。')
                journal = dict(task_id=self.req['task_id'], target=new, phase='prepared', reopen=False)
                d.write(journal_path, journal)
            if journal['phase'] == 'rollback':
                self.rollback(journal, journal['error'])
                journal_path.unlink(missing_ok=True)
                return
            new = journal['target']
            candidate = d.verify_release(location(self.root, new), new['commit'])
            if journal['phase'] == 'prepared':
                self.record('draining', '等待在途 AI 和发送任务结束')
                if self.req['running']: drain(self.data, previous, self.req['task_id'])
                exe = str(previous / 'WeChatAgentBridge.exe').lower()
                journal['reopen'] = any((p.info['exe'] or '').lower() == exe for p in psutil.process_iter(['exe']))
                d.close_launcher(previous)
                journal['phase'] = 'stopping'
                d.write(journal_path, journal)
            if journal['phase'] == 'stopping':
                self.record('backing_up', '正在正常停止后台并校验数据备份')
                d.stop(self.data, previous)
                (self.data / 'update-drain.json').unlink(missing_ok=True)
                check_data(self.data, d.compatible_schemas(candidate))
                backup = self.root / 'backups' / self.req['task_id'] / 'data'
                d.cold_backup(self.data, backup, validate=check_data)
                journal['phase'] = 'switching'
                d.write(journal_path, journal)
            if journal['phase'] == 'switching':
                self.record('switching', '正在切换程序版本')
                if self.req['running']: d.write(self.data / 'deployment-hold.json', {'task_id': self.req['task_id']})
                d.write(self.root / 'current.json', new)
                journal['phase'] = 'validating'
                d.write(journal_path, journal)
            if journal['phase'] == 'validating':
                if self.req['running']:
                    self.record('validating', '正在验证新版本，业务暂未恢复')
                    self.start_healthy(new, False)
                    check_data(self.data, candidate.get('database_output_schemas', [candidate['database_schema']]))
                journal['phase'] = 'activating'
                d.write(journal_path, journal)
            (self.data / 'deployment-hold.json').unlink(missing_ok=True)
            if self.req['running']:
                self.record('activating', '正在恢复业务并检查服务')
                self.start_healthy(new, True)
            self.remember(new)
            self.record('succeeded', '升级成功。' + ('后台已恢复。' if self.req['running'] else '后台保持停止。'))
            reopen = journal['reopen']
            journal_path.unlink(missing_ok=True)
            # Old cache described the version we just installed.
            (self.state / 'feed.json').unlink(missing_ok=True)
        except Exception as exc:
            error = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else '更新操作未完成（' + type(exc).__name__ + '），请检查网络、目录权限及日志。'
            (self.data / 'update-drain.json').unlink(missing_ok=True)
            if journal and journal['phase'] not in ('prepared',):
                journal.update(phase='rollback', error=error)
                d.write(journal_path, journal)
                try:
                    self.rollback(journal, error)
                    reopen = journal.get('reopen', False)
                    journal_path.unlink(missing_ok=True)
                except Exception as recovery:
                    self.record('failed', '自动回退未完成，请重新打开启动器恢复：' + str(recovery))
            else:
                self.record('failed', error)
                journal_path.unlink(missing_ok=True)
        if reopen:
            release = location(self.root, d.read(self.root / 'current.json'))
            os.startfile(release / 'WeChatAgentBridge.exe')

    def remember(self, new):
        path = self.state / 'history.json'
        history = d.read(path, [dict(directory=self.old['directory'], backup=None)])
        history = [x for x in history if x['directory'] != new['directory']]
        history.append(dict(directory=new['directory'], backup=self.req['task_id']))
        keep = history[-3:]
        d.write(path, keep)
        for entry in history[:-3]:
            for category in ('releases', 'controller'):
                try: d.remove(self.root / category, self.root / category / entry['directory'])
                except OSError: pass
            if entry['backup']:
                try: d.remove(self.root / 'backups', self.root / 'backups' / entry['backup'])
                except OSError: pass


def work(root):
    engine = Engine(root)
    mutex = engine.state / 'worker'
    mutex.mkdir(exist_ok=True)
    with d.lock(mutex):
        task = d.read(engine.state / 'task.json', {})
        if not task or (task['stage'] in TERMINAL and not (engine.state / 'transaction.json').exists()): return
        d.write(engine.state / 'worker.json', dict(pid=os.getpid(), created=psutil.Process().create_time()))
        try: engine.apply()
        finally: (engine.state / 'worker.json').unlink(missing_ok=True)


if __name__ == '__main__':
    work(Path(sys.argv[1]))
