"""Explicit own-profile observation for the newly authorized Task View test.

Reads only the operator-opened own profile and the two named test targets.
Never sets a draft, selects a conversation, activates a window, or sends.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def observation_seconds(value):
    seconds = int(value)
    if not 15 <= seconds <= 180:
        raise argparse.ArgumentTypeError('observation must be 15 to 180 seconds')
    return seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hwnd', type=int, required=True)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--created', type=int, required=True)
    parser.add_argument('--account', required=True)
    parser.add_argument('--group', required=True)
    parser.add_argument('--private', required=True)
    parser.add_argument('--allow-accessibility-patch', action='store_true', required=True)
    parser.add_argument('--observe-seconds', type=observation_seconds, default=45)
    args = parser.parse_args()
    from wechat_agent_bridge.adapters.accessibility_gate import AccessibilityGate, NativeGate
    from wechat_agent_bridge.adapters.windows import WindowsAdapter
    from wechat_agent_bridge.console_store import ConsoleStore
    from wechat_agent_bridge.console_models import ConsoleError
    from wechat_agent_bridge.protection import DPAPIProtector
    directory = ROOT / '.local/live-acceptance'
    store = native = adapter = gate = None
    report = {'state': 'not_verified', 'draft_attempted': False, 'send_invoked': False,
              'api_called': False, 'patch_restored': False, 'cleanup_errors': []}
    started = time.monotonic()
    try:
        store = ConsoleStore(directory / 'gate.sqlite3', DPAPIProtector())
        if store.get('patch', 'pending') or store.get('draft_probe', 'pending'):
            raise ConsoleError('prior_recovery_required')
        native = NativeGate(args.hwnd)
        fp = native.fingerprint()
        if fp.pid != args.pid or fp.created != args.created or fp.hwnd != args.hwnd:
            raise ConsoleError('selected_process_identity_changed')
        adapter = WindowsAdapter(timeout=3)
        gate = AccessibilityGate(native, allowed=args.allow_accessibility_patch,
                                 save=lambda item: store.put('patch', 'pending', item))
        # This readback is not a controls/capability claim. Own-profile validation
        # below must succeed before this process can persist an account binding.
        report['patch'] = gate.enable(fp, lambda: native.fingerprint() == fp)
        report['observation_seconds'] = args.observe_seconds
        print(json.dumps({'state': 'observing_operator_opened_own_profile', 'seconds': args.observe_seconds}), flush=True)
        until = time.monotonic() + args.observe_seconds
        while time.monotonic() < until:
            if native.fingerprint() != fp:
                raise ConsoleError('selected_process_identity_changed')
            try:
                account = adapter._call('verify_own_profile', fp.pid, fp.hwnd, args.account)
                break
            except ConsoleError as exc:
                if str(exc) != 'account_identity_uncertain_open_own_profile':
                    raise
                time.sleep(.3)
        else:
            raise ConsoleError('own_profile_observation_expired')
        if native.fingerprint() != fp:
            raise ConsoleError('selected_process_identity_changed')
        if (account.get('wechat_id') != args.account or
                account.get('instance_id') != f'{fp.pid}:{fp.hwnd}' or
                account.get('identity_source') != 'uia_own_profile_exact_id_user_selected_window'):
            raise ConsoleError('account_binding_reply_mismatch')
        store.put('binding', 'account', {'account': account, 'fingerprint': asdict(fp)})
        report['profile_identity'] = {'matched': True, 'source': account['identity_source']}
        report['target_controls'] = adapter._call('target_control_probe', fp.pid, fp.hwnd,
                                                  [args.group, args.private])
        target = report['target_controls']
        report['state'] = ('profile_verified' if target.get('identity_verified') is True and
            target.get('target_index') == 0 and target.get('draft_empty') is True and
            target.get('value_readonly') is False else 'profile_verified_target_not_ready')
    except BaseException as exc:
        report['reason'] = str(exc) if isinstance(exc, ConsoleError) else type(exc).__name__
    finally:
        if gate:
            try:
                result = gate.restore()
                report['restore'] = result
                report['patch_restored'] = result['state'] in ('restored', 'unchanged')
            except BaseException:
                report['state'] = 'recovery_required'
        for label, resource in (('adapter', adapter), ('native', native), ('store', store)):
            if resource:
                try:
                    resource.close()
                except BaseException:
                    report['cleanup_errors'].append(label + '_close_failed')
        if report['cleanup_errors'] or (gate and not report['patch_restored']):
            report['state'] = 'recovery_required'
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        path = directory / ('desktop2-binding-' + uuid.uuid4().hex + '.json')
        with path.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2)
        print(json.dumps({'state': report['state'], 'reason': report.get('reason'),
                          'patch_restored': report['patch_restored'], 'evidence': str(path)}))
    return 0 if report['state'] == 'profile_verified' and report['patch_restored'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
