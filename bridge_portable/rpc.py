"""One local GUI request through anonymous pipes; no socket or secret arguments."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys

# Resolve beside the executable, never relative to the working directory.
ROOT = Path(__file__).resolve().parents[1]
from bridge_paths import DEFAULT_DATA
os.environ['BRIDGE_DATA_DIR'] = str(DEFAULT_DATA)


def dispatch(request):
    from . import control
    action = request.get('action')
    if action == 'bootstrap':
        from .deploy import read, launch_console
        from .updates import installation
        from .update_engine import bootstrap
        if installation(ROOT) != ROOT and installation(ROOT) is not None: return 'continue'
        manifest = read(ROOT / 'portable-manifest.json', {})
        if not manifest or manifest.get('development'): return 'continue'
        release = bootstrap(ROOT)
        launch_console(release, background=request.get('background') is True)
        return 'opened'
    if action in ('update-check', 'update-auto', 'update-status', 'update-install'):
        from .updates import Updates, TERMINAL
        updates = Updates()
        if action == 'update-install': state = updates.install(request['version'], request['release_id'])
        elif action == 'update-status': state = updates.query()
        else: state = updates.check(force=action == 'update-check')
        latest, task = state['latest'], state['task']
        lines = ['当前版本：' + state['current_version'], '发布页：' + state['releases_url']]
        token = ''
        if state['mode'] == 'managed': lines.append('本机由服务器自动部署管理。')
        elif state['mode'] == 'source': lines.append('源码或开发模式：请从发布页获取正式便携包。')
        elif latest:
            lines.extend(['发现 v' + latest['version'] + ' · ' + latest['published_at'], latest['notes'], latest.get('reason') or ''])
            if latest['ready']: token = latest['version'] + ':' + str(latest['release_id'])
        elif not state['error']: lines.append('当前没有可用的新版。')
        if state['error']: lines.append(state['error'])
        pending = bool(task and task['stage'] not in TERMINAL)
        if task:
            lines.append(task['message'])
            if task.get('total'): lines.append('下载进度：' + str(task['downloaded'] * 100 // task['total']) + '%')
        return ('UPDATE:' + ('busy' if pending else token) + '\n' + '\n'.join(lines))
    if action in ('start', 'stop', 'setup', 'reset', 'export', 'export-config', 'import'):
        from .updates import Updates, TERMINAL
        from .deploy import read
        updates = Updates()
        if updates.mode == 'managed':
            if ((updates.root / 'transaction.json').exists() or (control.DATA / 'deployment-hold.json').exists()):
                raise ValueError('服务器正在部署，请等待完成后再操作。')
            if read(updates.root / 'current.json')['commit'] != ROOT.name:
                raise ValueError('此控制台版本已过期，请从安装根目录重新打开。')
        if updates.mode == 'portable':
            from .deploy import read
            task = updates.query()['task']
            if (task and task['stage'] not in TERMINAL) or (updates.directory / 'transaction.json').exists():
                raise ValueError('更新任务尚未结束，请稍后再操作。')
            if read(updates.root / 'current.json')['directory'] != ROOT.name:
                raise ValueError('此启动器版本已过期，请从安装根目录重新打开。')
    username = request.get('username') or 'admin'
    password = request.get('password', '')
    confirmation = request.get('confirmation', '')
    if action == 'status':
        state = control.status()
        access = ('SSH 私人访问：' + ('已就绪，仅监听服务器本机' if state['private_access'] else '尚未启动或未就绪') if state['access_mode'] == 'private' else
                  'HTTPS：' + ('本机证书/访问检查通过（公网访问需外部设备确认）' if state['https'] else '未通过或未启动'))
        return ('后台：' + ('运行中' if state['running'] else '已停止') + '\n' + access +
                '\n地址：' + control.config().get('origin','尚未配置') +
                '\n迁移：' + ('等待自动检查' if state['migration_pending'] else '无待恢复迁移'))
    if action == 'setup':
        return control.setup(request.get('origin',''), username, password, confirmation, private=request.get('private') is True)
    if action == 'reset':
        return control.reset(password, confirmation)
    if action == 'start':
        return control.start()
    if action == 'stop':
        return control.stop()
    if action == 'check':
        return control.check()
    if action in ('open', 'logs', 'help'):
        target = control.config().get('origin') if action=='open' else str(control.DATA/'production') if action=='logs' else str(ROOT/'使用说明.txt')
        if not target:
            raise ValueError('请先选择访问方式并保存首次配置。')
        if action=='logs':
            (control.DATA/'production').mkdir(parents=True, exist_ok=True)
        os.startfile(target)
        return '已打开。'
    if action in ('export','export-config','import'):
        from .migrate import export_data, import_data
        from .configuration import export_configuration
        path = request.get('path')
        if not path:
            raise ValueError('未选择文件。')
        control.stop()
        value = (export_data(control.DATA,path) if action=='export' else
                 export_configuration(control.DATA,path) if action=='export-config' else import_data(path,control.DATA))
        return json.dumps(value, ensure_ascii=False, indent=2)+'\n后台保持停止。'
    raise ValueError('未知操作。')


def main():
    try:
        raw = sys.stdin.buffer.read(32769)
        if len(raw)>32768:
            raise ValueError('请求过大。')
        request = json.loads(raw)
        # Diagnostic helpers may print. Do not mix that into the GUI protocol.
        with contextlib.redirect_stdout(io.StringIO()):
            if request.get('action') in ('start','stop','setup','reset','export','export-config','import'):
                from .updates import Updates
                from .deploy import lock
                updates = Updates()
                # Serialize a long import/export/start with accepting a new update.
                guard = (lock(updates.root) if updates.mode == 'managed' else
                         lock(updates.directory) if updates.mode == 'portable' else contextlib.nullcontext())
                with guard: message = dispatch(request)
            else: message = dispatch(request)
        print(message, flush=True)
        return 0
    except Exception as exc:
        # No repr(request), traceback, command line or credentials.
        safe = str(exc) if isinstance(exc,(ValueError,RuntimeError)) else '操作失败（'+type(exc).__name__+'），请检查程序完整性、目录权限和日志。'
        print(safe, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
