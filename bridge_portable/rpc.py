"""One local GUI request through anonymous pipes; no socket or secret arguments."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys

# Resolve beside the executable, never relative to the working directory.
ROOT = Path(__file__).resolve().parents[1]
os.environ['BRIDGE_DATA_DIR'] = str(ROOT/'data')


def dispatch(request):
    from . import control
    action = request.get('action')
    username = request.get('username') or 'admin'
    password = request.get('password', '')
    confirmation = request.get('confirmation', '')
    if action == 'status':
        state = control.status()
        access = ('SSH 私人访问：' + ('已就绪，仅监听服务器本机' if state['private_access'] else '尚未启动或未就绪') if state['access_mode'] == 'private' else
                  'HTTPS：' + ('本机证书/访问检查通过（公网访问需外部设备确认）' if state['https'] else '未通过或未启动'))
        return ('后台：' + ('运行中' if state['running'] else '已停止') + '\n' + access +
                '\n地址：' + control.config().get('origin','尚未配置') +
                '\n迁移：' + ('等待验证和手动恢复' if state['migration_pending'] else '无待恢复迁移'))
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
    if action=='acceptance':
        if request.get('confirmed') is not True:
            raise ValueError('发送验收需要用户明确确认。')
        return control.acceptance(username,password)
    if action=='resume':
        return control.resume(username,password)
    raise ValueError('未知操作。')


def main():
    try:
        raw = sys.stdin.buffer.read(32769)
        if len(raw)>32768:
            raise ValueError('请求过大。')
        request = json.loads(raw)
        # Diagnostic helpers may print. Do not mix that into the GUI protocol.
        with contextlib.redirect_stdout(io.StringIO()):
            message = dispatch(request)
        print(message, flush=True)
        return 0
    except Exception as exc:
        # No repr(request), traceback, command line or credentials.
        safe = str(exc) if isinstance(exc,(ValueError,RuntimeError)) else '操作失败（'+type(exc).__name__+'），请检查程序完整性、目录权限和日志。'
        print(safe, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
