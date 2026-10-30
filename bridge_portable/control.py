"""Local management operations used by the Win32 launcher."""
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlsplit
import httpx
import psutil

from bridge_paths import ROOT, DATA, FRONTEND, migration_pending
from backend.app.auth import AuthStore
from scripts.production_server import render_config
from .local_api import LocalApi

CONFIG = DATA / 'portable-config.json'


def config():
    return json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.is_file() else {}


def environment():
    # Never inherit a developer/test database or dev auth mode in a portable launch.
    return {**os.environ, 'BRIDGE_DATA_DIR': str(DATA), 'BRIDGE_ENV': config().get('mode', 'production'),
            'BRIDGE_AUTH_DATABASE': str(DATA/'auth.sqlite3'),
            'BRIDGE_PUBLIC_ORIGIN': config().get('origin', ''),
            'BRIDGE_CADDY': str(ROOT/'bin/caddy.exe')}


def owner():
    path = DATA / 'production/current.json'
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    try:
        process = psutil.Process(value['pid'])
        if abs(process.create_time()-value['created']) > .01:
            return None
        script = str(ROOT/'scripts/production_server.py').lower()
        if script not in [a.lower() for a in process.cmdline()]:
            raise ValueError('记录的进程不属于本程序，未操作该进程。')
        return process, value
    except psutil.NoSuchProcess:
        return None


def status():
    current = owner()
    private = config().get('mode') == 'private'
    value = dict(running=bool(current), https=False, private_access=False, access_mode='private' if private else 'production', configured=bool(config()), migration_pending=migration_pending())
    if current:
        try:
            with httpx.Client(trust_env=False, timeout=2) as client:
                result = client.get('http://127.0.0.1:8765/api/v1/health')
                value['backend'] = result.is_success and result.json().get('service') == 'wechat-agent-bridge'
                result = client.get(config()['origin']+'/api/v1/health')
                value['private_access' if private else 'https'] = result.is_success and result.json().get('service') == 'wechat-agent-bridge'
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    return value


def setup(origin, username, password, confirmation, *, private=False):
    if owner():
        raise ValueError('请先停止程序再修改配置。')
    origin = 'http://127.0.0.1:18765' if private else origin.strip().rstrip('/')
    if not private and not origin.startswith('https://'):
        origin = 'https://' + origin
    mode = 'private' if private else 'production'
    render_config(origin, FRONTEND, mode)
    if password != confirmation:
        raise ValueError('两次密码不一致。')
    DATA.mkdir(parents=True, exist_ok=True)
    auth = AuthStore(DATA/'auth.sqlite3')
    try:
        if not auth.initialized():
            auth.set_password(password, username=username or 'admin')
        elif password:
            raise ValueError('已有管理员。修改域名请留空密码；修改密码请使用“重置密码”。')
    finally:
        auth.close()
    temporary = CONFIG.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(origin=origin, mode=mode), indent=2), encoding='utf-8')
    os.replace(temporary, CONFIG)
    return '私人访问已配置。无需域名；启动后仅本机或 SSH 隧道可访问。' if private else '配置已保存。请确认 DNS 与 80/443 端口已就绪，然后启动。'


def reset(password, confirmation):
    if password != confirmation:
        raise ValueError('两次密码不一致。')
    auth = AuthStore(DATA/'auth.sqlite3')
    try:
        auth.set_password(password, reset=True)
    finally:
        auth.close()
    return '密码已重置，旧网页登录会话已注销。'


def check():
    if sys.platform != 'win32' or sys.maxsize <= 2**32 or sys.getwindowsversion().build < 17763:
        raise ValueError('需要 Windows 10 1809 / Server 2019 或更新的 x64 桌面系统。')
    DATA.mkdir(parents=True, exist_ok=True)
    import tempfile
    with tempfile.TemporaryFile(dir=DATA):
        pass
    from wechat_sender.compatibility import build_identity
    build_identity()
    if not (FRONTEND/'index.html').is_file() or not (ROOT/'bin/caddy.exe').is_file():
        raise ValueError('程序文件不完整，请重新解压完整 ZIP。')
    from wechat_account_probe.native import enumerate_instances
    instances, issues = enumerate_instances()
    from wechat_compatibility import profile_for_hash
    versions = []
    for instance in instances:
        profile = profile_for_hash(instance.core_sha256)
        caps = profile['capabilities'] if profile else {}
        versions.append(instance.version + '：' + ('读取已适配' if caps.get('messages') else '读取未适配') +
                        '，' + ('发送组件已适配，仍需本机验收' if caps.get('send_text') else '发送未适配'))
    if issues:
        versions.append('有实例无法确认，请重新检查')
    return 'Windows/目录/程序文件检查通过。\n微信：' + ('；'.join(versions) or '未运行') + '\n兼容暂停与恢复请在后台账号页面操作。'


def start():
    if owner():
        return '后台已经运行。'
    check()
    env = environment()
    command = [sys.executable, '-B', '-X', 'utf8', str(ROOT/'scripts/production_server.py')]
    result = subprocess.run([*command, '--check'], env=env, cwd=ROOT, capture_output=True,
                            encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ValueError((result.stderr or result.stdout)[-3000:])
    logs = DATA/'production'; logs.mkdir(exist_ok=True)
    with (logs/('supervisor-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.log')).open('wb') as output:
        child = subprocess.Popen(command, env=env, cwd=ROOT, stdin=subprocess.DEVNULL,
                                 stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic()+45
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise ValueError('后台启动失败，请查看 production 日志。')
        if owner():
            value = status()
            if value.get('backend'):
                if value['access_mode'] == 'private':
                    if value['private_access']:
                        return '后台已启动，仅本机或 SSH 隧道可访问 http://127.0.0.1:18765。'
                    time.sleep(.5)
                    continue
                return '后台已启动。' + ('HTTPS 检查通过。' if value['https'] else 'HTTPS 尚未就绪，请检查 DNS、80/443 与 Caddy 日志。')
        time.sleep(.5)
    raise ValueError('后台启动仍在进行，请刷新状态或查看日志。')


def stop():
    current = owner()
    if not current:
        return '后台未运行。'
    process, value = current
    stop_file = Path(value['stop_file']).resolve()
    if not stop_file.is_relative_to((DATA/'production').resolve()) or stop_file.name != 'stop.request':
        raise ValueError('停止记录无效，未操作任何进程。')
    stop_file.write_text('stop\n', encoding='utf-8')
    # Do not kill an in-flight model/send operation. GUI stays responsive.
    from backend.app.ai_timeouts import AI_SHUTDOWN_GRACE_SECONDS
    try:
        process.wait(AI_SHUTDOWN_GRACE_SECONDS + 15)
    except psutil.TimeoutExpired:
        raise ValueError('等待正常停止超时；请查看日志，未强制结束进程。') from None
    return '后台已停止。'


def acceptance(username, password):
    if not owner():
        raise ValueError('请先启动后台并等待微信识别完成。')
    from scripts.verify_native_send_live import run_acceptance
    from wechat_sender.compatibility import REQUIRED_CHECKS, ACCEPTANCE, machine_binding
    with LocalApi(config()['origin'], username, password) as api:
        summary, report = run_acceptance(api)
    if not summary['passed'] or summary.get('drafts_unchanged') is not True:
        return '验收未完全通过，自动发送仍被阻止。请按说明准备可观察的草稿并查看：'+str(report)
    from wechat_compatibility import profile_for_hash, profile_digest
    try:
        previous = json.loads(ACCEPTANCE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    records = [r for r in previous.get('records',[]) if r.get('account') not in summary['accounts']] if previous.get('version') == 2 else []
    for account, sample in zip(summary['accounts'], summary['samples'], strict=True):
        identity = sample['result']; profile = profile_for_hash(identity['core_sha256'])
        records.append(dict(passed=True, account=account, core_sha256=identity['core_sha256'],
                            profile_digest=profile_digest(profile), dll_sha256=summary['dll_sha256'], binding=machine_binding(),
                            checks={name:'passed' for name in REQUIRED_CHECKS}, report=str(report.relative_to(DATA))))
    record = dict(version=2, records=records)
    temp = ACCEPTANCE.with_suffix('.tmp')
    temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, ACCEPTANCE)
    return '本机发送验收通过。请在后台的兼容状态中明确恢复；迁移数据需点击“恢复迁移”。'


def resume(username, password):
    if not migration_pending():
        raise ValueError('没有等待恢复的迁移。')
    with LocalApi(config()['origin'], username, password) as api:
        compatibility = api.post('compatibility/query')
        if compatibility['held']:
            raise ValueError('仍有未确认的微信实例，请先完成重新识别。')
        accounts = api.post('accounts/query')['accounts']
        ready = []
        with closing(sqlite3.connect((DATA/'bridge.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
            enabled = db.execute('SELECT account_id,conversation_id FROM conversation_preferences WHERE ai_enabled=1').fetchall()
        needed = {aid for aid,_ in enabled}
        for account in accounts:
            aid = account['account_id']
            if aid not in needed:
                continue
            if len(account.get('instances', [])) != 1 or account.get('sync_pending') or account.get('sync_issues'):
                raise ValueError('请等待所有启用 AI 的账号在线且基线同步完成。')
            if not api.post('wechat-send-status/query', account_id=aid)['ready']:
                raise ValueError('仍有账号未完成本机发送验收。')
            ready.append(aid)
        for aid,cid in enabled:
            if not api.post('conversation-preferences/query',account_id=aid,conversation_id=cid)['effective']['ready']:
                raise ValueError('仍有会话需要重新填写 API 密钥或配置角色卡。')
    stop()
    from .migrate import resume_data
    resume_data(DATA, ready)
    return start()
