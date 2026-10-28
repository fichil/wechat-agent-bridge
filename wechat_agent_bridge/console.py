"""Command-line launch, shutdown and local browser lifecycle."""

import argparse
import os
from pathlib import Path
import webbrowser

from .adapters.console_mock import MockConsoleAdapter
from .adapters.windows import WindowsAdapter
from .console_http import ConsoleServer
from .console_service import ConsoleService
from .console_store import ConsoleStore
from .console_models import ConsoleError
from .openai_config import configure_openai, load_openai_configuration
from .protection import DPAPIProtector
from .acceptance import AcceptanceLedger, AcceptanceSession, acceptance_directory


def default_adapter():
    return 'windows' if os.name == 'nt' else 'mock'


def main(argv=None):
    parser = argparse.ArgumentParser(description="本地微信控制台（Windows 默认发现本机账号；--adapter mock 合成演示）")
    parser.add_argument("--adapter", choices=("mock", "windows"), default=default_adapter())
    parser.add_argument('--discovery-mode', choices=('read-only', 'profile'),
                        help='Windows 普通启动默认 read-only；profile 显式选择自动开本人资料卡')
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--live-acceptance", action="store_true", help="本轮受限真实验收；重启保持暂停与累计额度")
    parser.add_argument("--allow-accessibility-patch", action="store_true", help="允许在控制台绑定窗口后临时启用进程无障碍字节")
    parser.add_argument("--openai-env-file", help="显式导入 OpenAI 密钥；仅用于 mock 或 --live-acceptance")
    parser.add_argument('--provider-preset', choices=('deepseek',), help='明确切换模型服务；密钥在控制台填写')
    parser.add_argument('--test-account', help='明确本轮获准的发送微信号')
    parser.add_argument('--test-group', help='明确本轮获准的测试群；页面不能扩大范围')
    parser.add_argument('--prefix-reply-trial', '--reply-trial', action='store_true', help='显式允许当前绑定群的文本自动回复试运行')
    parser.add_argument('--reply-trigger', choices=('prefix','all_new_text'), default='prefix', help='试运行触发范围：前缀或全部新文字')
    parser.add_argument('--reply-send-mode', choices=('isolated','main-window'), default='isolated',
                        help='main-window 显式恢复旧主窗口发送；需要目标群、空稿及后台条件')
    parser.add_argument('--hook-recovery-evidence', help='同构建临时发送helper的本机合成恢复证据')
    parser.add_argument('--trial-send-slot', action='append', default=[], help='开发者已经在原账本追加的发送槽位')
    parser.add_argument('--no-test-budget', action='store_true',
                        help='取消累计验收额度及固定发送槽位；保留调用审计和常规限频')
    args = parser.parse_args(argv)
    if args.discovery_mode and args.adapter != 'windows':
        parser.error('discovery_mode_requires_windows_adapter')
    if args.discovery_mode == 'read-only' and (args.live_acceptance or args.allow_accessibility_patch):
        parser.error('read_only_discovery_conflicts_with_live_operations')
    if args.reply_trigger != 'prefix' and not args.prefix_reply_trial:
        parser.error('reply_trigger_requires_explicit_trial')
    if args.reply_send_mode != 'isolated' and not args.prefix_reply_trial:
        parser.error('reply_send_mode_requires_explicit_trial')
    if (args.prefix_reply_trial or args.hook_recovery_evidence or args.trial_send_slot or args.no_test_budget) and not (
            args.prefix_reply_trial and args.hook_recovery_evidence and (args.trial_send_slot or args.no_test_budget) and
            args.live_acceptance and args.allow_accessibility_patch and args.adapter == 'windows'):
        parser.error('prefix_trial_requires_explicit_helper_and_acceptance')
    if args.openai_env_file and args.provider_preset:
        parser.error('provider_selection_conflict')
    if (args.test_account or args.test_group) and (not args.test_account or not args.test_group or
            not args.live_acceptance or not args.allow_accessibility_patch):
        parser.error('test_group_requires_bound_windows_acceptance')
    if (args.live_acceptance or args.allow_accessibility_patch) and args.adapter != "windows":
        parser.error("acceptance_requires_windows_adapter")
    if args.allow_accessibility_patch and not args.live_acceptance:
        parser.error("accessibility_patch_requires_acceptance")
    if args.openai_env_file:
        if args.adapter != "mock" and not args.live_acceptance:
            parser.error("openai_test_requires_mock")
        try:
            load_openai_configuration(args.openai_env_file)
        except ConsoleError as exc:
            parser.error(str(exc))
    if args.live_acceptance and args.data_dir:
        parser.error("acceptance_data_directory_fixed")
    directory = (acceptance_directory() if args.live_acceptance else
                 Path(args.data_dir or (".local/openai-test" if args.openai_env_file else ".local/console")).resolve())
    directory.mkdir(parents=True, exist_ok=True)
    # Probe protection before opening a file or accepting any sensitive content.
    protector = DPAPIProtector()
    if protector.unprotect(protector.protect(b"probe")) != b"probe":
        raise RuntimeError("local_protection_failed")
    store = ConsoleStore(directory/(args.adapter+"-state.sqlite3"), protector)
    ledger = AcceptanceLedger(directory/"quota.sqlite3") if args.live_acceptance else None
    gate_store = ConsoleStore(directory/"gate.sqlite3", protector) if args.live_acceptance else None
    adapter = MockConsoleAdapter() if args.adapter == "mock" else WindowsAdapter(
        allow_accessibility_patch=args.allow_accessibility_patch, gate_store=gate_store,
        trial_send_mode=args.reply_send_mode, auto_discovery=not args.live_acceptance,
        discovery_mode=args.discovery_mode or ('profile' if args.live_acceptance else 'read-only'))
    if args.prefix_reply_trial:
        adapter.trial_evidence = str(Path(args.hook_recovery_evidence).resolve())
    service = ConsoleService(adapter, store, acceptance=AcceptanceSession(
        ledger, provider_profile=args.provider_preset or 'openai',
        test_budget_enforced=not args.no_test_budget) if ledger else None,
        test_account=args.test_account, test_group=args.test_group, trial_slots=args.trial_send_slot,
        trial_trigger=args.reply_trigger)
    if args.provider_preset:
        service.select_provider_preset(args.provider_preset)
    if args.openai_env_file:
        try:
            configure_openai(service, args.openai_env_file)
        except ConsoleError as exc:
            service.close()
            store.close()
            if ledger:
                ledger.close()
            if gate_store:
                gate_store.close()
            parser.error(str(exc))
    server = ConsoleServer(service, args.port)
    print("仅本机访问；关闭命令行或按 Ctrl+C 停止。", flush=True)
    print("合成演示，不连接真实微信。" if adapter.simulation_only else "自动读取本机微信昵称与微信号；聊天及自动运行保持暂停。", flush=True)
    print(server.launch_url, flush=True)
    service.start()
    if not args.no_browser and not getattr(adapter, 'metadata_only', False):
        try:
            if not webbrowser.open(server.launch_url):
                print("浏览器未自动打开，请使用上面的本机地址。", flush=True)
        except Exception:
            print("浏览器未自动打开，请使用上面的本机地址。", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            service.close()
        finally:
            server.server_close()
            store.close()
            if ledger:
                ledger.close()
            if gate_store:
                gate_store.close()
    return 0
