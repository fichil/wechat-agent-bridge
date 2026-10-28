"""Opt-in Invoke calibration using only an owned Button on a private desktop.

Reuses the frozen GUI lifecycle without editing it. One owned Edit is replaced
by a standard BS_NOTIFY Button; its parent records clicks and focus notifications.
No external target, live client, debugger, clipboard or input desktop operation.
"""
import argparse
import ctypes as c
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import probe_uia_autofocus_tree as tree
from scripts.uia_context_evidence import inspect_context
from scripts.uia_context_native import SelfMemory
from scripts.owned_notification_stack import OwnStackCapture

base = tree.base
PHASES = ('default', 'false')
TOKEN = 'owned-button-invoked'
ORIGINAL_CHILD, ORIGINAL_FINGERPRINTS = tree._ORIGINAL_CHILD, base.fingerprints


def fingerprints():
    result = ORIGINAL_FINGERPRINTS()
    for name in ('scripts/probe_uia_invoke.py', 'scripts/owned_notification_stack.py',
                 'scripts/probe_uia_autofocus_tree.py', 'scripts/uia_context_evidence.py',
                 'scripts/uia_context_native.py'):
        result[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    return result


def qualified(result):
    if not isinstance(result, dict) or result.get('error') or result.get('mta_verified') is not True:
        return False
    phases, target = result.get('phases', []), result.get('target', {})
    if [p.get('phase') for p in phases] != list(PHASES):
        return False
    for p in phases:
        if (type(p.get('clicks')) is not int or p['clicks'] != 1 or
                p.get('invoke_returned') is not True or p.get('button_event_overflow') is not False or
                p.get('native_full_readback_equal') is not True or
                p.get('uia_full_readback_equal') is not True or p.get('identity_stable') is not True or
                p.get('context', {}).get('stable') is not True or
                p.get('context', {}).get('matches_requested_client') is not True or
                p.get('focus_before') != target.get('b') or p.get('event_overflow')):
            return False
    return (phases[0].get('focus') == target.get('a') and
            isinstance(phases[0].get('button_focus_events'), list) and
            'set' in phases[0]['button_focus_events'] and
            phases[0]['context'].get('associated_auto_focus') is True and
            phases[1].get('focus') == target.get('b') and
            phases[1].get('button_focus_events') == [] and
            phases[1]['context'].get('associated_auto_focus') is False)


def observations(result):
    """Separate execution evidence from the stricter no-focus qualification."""
    phases = result.get('phases', []) if isinstance(result, dict) else []
    if (len(phases) != 2 or [p.get('phase') for p in phases] != list(PHASES) or
            result.get('error') or result.get('mta_verified') is not True):
        return {'invocation_observed': False, 'false_client_focus_unchanged': None}
    executed = all(type(p.get('clicks')) is int and p['clicks'] == 1 and
        all(p.get(k) is True for k in ('invoke_returned', 'native_full_readback_equal',
                                     'uia_full_readback_equal', 'identity_stable')) for p in phases)
    return dict(invocation_observed=executed,
        false_client_focus_unchanged=(phases[1].get('focus') == result.get('target', {}).get('b') and
            phases[1].get('button_focus_events') == []) if executed else None)


def receiver_relation(phase):
    """Observe a GUI receiver chain; never infer a cross-thread sender stack."""
    if not isinstance(phase, dict) or phase.get('stack_error') is not False:
        return False
    records = phase.get('notification_stacks', [])
    if not isinstance(records, list) or len(records) > 16:
        return False
    # Each nested entry must precede the next on the same recorded GUI thread.
    steps = ((245, None, [245]), (513, None, [245, 513]),
             (7, None, [245, 513, 7]), (None, 6, [245, 513, 7]))
    cursor, thread = 0, None
    for row in records:
        if not isinstance(row, dict):
            return False
        expected = steps[cursor]
        if (row.get('stage') == 'invoke' and
                (row.get('button_message'), row.get('notification'),
                 row.get('active_button_messages')) == expected and
                type(row.get('thread_id')) is int and row['thread_id'] > 0):
            if thread is not None and row['thread_id'] != thread:
                return False
            thread = row['thread_id']
            cursor += 1
            if cursor == len(steps):
                return True
    return False


def child(pipe, desktop_id):
    # Wrappers exist only in this disposable child, before any window creation.
    original_call = base.call
    owned = {'button': None, 'clicks': 0, 'events': [], 'overflow': False,
             'stage': 'idle', 'stacks': [], 'capture': None, 'stack_error': False,
             'callbacks': [], 'active_messages': []}

    def capture_record(**metadata):
        if owned['stage'] != 'invoke':
            return
        if len(owned['stacks']) >= 16:
            owned['stack_error'] = True
            return
        try:
            record = owned['capture'].record()
            record.update(metadata, stage='invoke', active_button_messages=list(owned['active_messages']))
            owned['stacks'].append(record)
        except Exception:
            owned['stack_error'] = True

    def observe_owned_button(dll, hwnd):
        # Subclass ONLY the Button just returned by this child's CreateWindow.
        # No external HWND, focus policy change or interception outside fixture.
        proc_type = base.CALLBACK(c.c_ssize_t, base.PTR, base.U32, base.U64, c.c_ssize_t)
        forward = original_call(dll, 'CallWindowProcW', c.c_ssize_t,
            [base.PTR, base.PTR, base.U32, base.U64, c.c_ssize_t])
        previous = [None]

        def dispatch(button, message, wparam, lparam):
            watch = message in (0xF5, 0x201, 0x202, 7, 8) and owned['stage'] == 'invoke'
            if watch:
                owned['active_messages'].append(message)
                capture_record(button_message=message)
            try:
                return forward(previous[0], button, message, wparam, lparam)
            finally:
                if watch:
                    owned['active_messages'].pop()

        callback = proc_type(dispatch)
        setter = original_call(dll, 'SetWindowLongPtrW', base.PTR,
                               [base.PTR, c.c_int, base.PTR])
        previous[0] = setter(hwnd, -4, c.cast(callback, base.PTR))
        if not previous[0]:
            raise RuntimeError('owned_button_observer_failed')
        owned['callbacks'].append(callback)  # Pin until this child's windows die.

    def wrapped_call(dll, name, restype, argtypes):
        fn = original_call(dll, name, restype, argtypes)
        if name == 'CreateWindowExW':
            def create(*args):
                if args[1] == 'Edit' and owned['button'] is None:
                    args = list(args)
                    args[1], args[3] = 'Button', 0x50004000  # CHILD|VISIBLE|BS_NOTIFY
                    hwnd = fn(*args)
                    owned['button'] = hwnd
                    if hwnd:
                        observe_owned_button(dll, hwnd)
                    return hwnd
                return fn(*args)
            return create
        if name == 'DefWindowProcW':
            def dispatch(hwnd, message, wparam, lparam):
                if message == 0x111 and owned['button'] and lparam == owned['button']:
                    code = (wparam >> 16) & 0xffff
                    if code in (0, 6, 7) and owned['stage'] == 'invoke':
                        capture_record(notification=code)
                    if code == 0:  # BN_CLICKED, acknowledged independently by the GUI.
                        owned['clicks'] += 1
                        setter = original_call(dll, 'SetWindowTextW', c.c_int,
                                               [base.PTR, c.c_wchar_p])
                        if not setter(lparam, TOKEN):
                            owned['clicks'] = -100
                    elif code in (6, 7):  # BN_SETFOCUS / BN_KILLFOCUS
                        if len(owned['events']) < 16:
                            owned['events'].append('set' if code == 6 else 'kill')
                        else:
                            owned['overflow'] = True
                return fn(hwnd, message, wparam, lparam)
            return dispatch
        return fn

    def phase(create, uia, target, exchange, name, records):
        before = exchange('prepare', name)
        owned.update(clicks=0, events=[], overflow=False, stage='idle', stacks=[], stack_error=False)
        report = {'phase': name, 'focus_before': before['focus']}
        records.append(report)
        client = create(uia.CUIAutomation8, interface=uia.IUIAutomation2)
        if name == 'false':
            client.AutoSetFocus = 0
        flag = int(client.AutoSetFocus) != 0
        if flag != (name == 'default'):
            raise RuntimeError('invoke_client_configuration_mismatch')
        element = client.ElementFromHandle(target['a'])
        rid = tuple(element.GetRuntimeId())
        report['owned_button_metadata'] = dict(pid=element.CurrentProcessId,
            hwnd=element.CurrentNativeWindowHandle, control_type=element.CurrentControlType,
            name_length=len(element.CurrentName or ''), expected_button_type=uia.UIA_ButtonControlTypeId)
        if (element.CurrentProcessId != target['pid'] or
                element.CurrentNativeWindowHandle != target['a'] or
                element.CurrentControlType != uia.UIA_ButtonControlTypeId or element.CurrentName not in ('', None)):
            raise RuntimeError('owned_button_identity_mismatch')
        pattern = element.GetCurrentPattern(uia.UIA_InvokePatternId).QueryInterface(uia.IUIAutomationInvokePattern)
        memory = SelfMemory()
        try:
            report['context'] = inspect_context(memory, memory.image,
                c.cast(client, c.c_void_p).value, c.cast(element, c.c_void_p).value,
                c.cast(pattern, c.c_void_p).value, flag, pattern_kind='invoke')
            memory.validate_source()
        finally:
            memory.close()
        check = exchange('check', name)
        if check['focus'] != target['b'] or owned['clicks'] or owned['events']:
            raise RuntimeError('button_discovery_changed_state')
        owned['capture'] = OwnStackCapture()
        report['stack_module_fingerprints'] = dict(owned['capture'].fingerprints)
        owned['stage'] = 'invoke'
        pattern.Invoke()  # Only the Button created by this child.
        report['invoke_returned'] = True
        report.update(exchange('complete', name))
        owned['stage'] = 'idle'
        report.update(notification_stacks=list(owned['stacks']), stack_error=owned['stack_error'])
        report.update(clicks=owned['clicks'], button_focus_events=list(owned['events']),
            button_event_overflow=owned['overflow'],
            uia_full_readback_equal=element.CurrentName == TOKEN,
            identity_stable=tuple(element.GetRuntimeId()) == rid and
                element.CurrentProcessId == target['pid'] and element.CurrentNativeWindowHandle == target['a'])

    tree.require_base()
    with tree.replace_attributes(base, call=wrapped_call, phase=phase, PHASES=PHASES,
                                 TEXT={name: TOKEN for name in PHASES}):
        ORIGINAL_CHILD(pipe, desktop_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--run-synthetic', action='store_true')
    args = parser.parse_args(argv)
    if not args.run_synthetic:
        print(json.dumps({'state': 'disabled', 'live_client_verified': False}))
        return 0
    tree.require_base()
    with tree.replace_attributes(base, child=child, fingerprints=fingerprints, source_qualified=qualified):
        report = base.run()
    report.update(kind='owned_button_invoke', live_client_verified=False,
                  cross_process_verified=False, debugger_used=False)
    report.update(observations(report.get('result', {})))
    report['receiver_focus_inside_bm_click'] = {
        phase.get('phase', 'unknown'): receiver_relation(phase)
        for phase in report.get('result', {}).get('phases', [])}
    path = ROOT / '.local/live-acceptance' / ('uia-invoke-' + uuid.uuid4().hex + '.json')
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'path': str(path), 'passed': report['passed'], 'error': report.get('error')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
