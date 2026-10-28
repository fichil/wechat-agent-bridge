"""Opt-in AutoSetFocus canary on owned standard Edit controls only.

No client is opened by default. --run-synthetic uses a new private, non-input
desktop: legacy default, fresh UIAutomation8 default, then fresh
UIAutomation8(False). Visible controls exist only on that private desktop;
there is no SwitchDesktop, foreground request, input simulation or clipboard
write. This qualifies a synthetic source, never live-client compatibility.
"""
import argparse
import atexit
import ctypes as c
import gc
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import queue
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_focus_etw import (U32, U64, PTR, CALLBACK, call, desktop_name,
                            input_snapshot, create_private_desktop)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
PHASES = ('legacy_default', 'modern_default', 'modern_no_focus')
TEXT = dict(zip(PHASES, ('m1-legacy', 'm1-modern', 'm1-no-focus')))
CHILD_TIMEOUT = 20
COMTYPES_VERSION = '1.4.17'


class WindowClass(c.Structure):
    _fields_ = [('size', U32), ('style', U32), ('proc', PTR),
                ('class_extra', c.c_int), ('window_extra', c.c_int),
                ('instance', PTR), ('icon', PTR), ('cursor', PTR),
                ('background', PTR), ('menu', c.c_wchar_p),
                ('name', c.c_wchar_p), ('small_icon', PTR)]


def identity(element, target):
    """Never use names, or inspect an element outside our exact owned HWND."""
    if (element.CurrentProcessId != target['pid'] or
            element.CurrentNativeWindowHandle != target['a']):
        raise RuntimeError('synthetic_element_identity_mismatch')
    runtime = tuple(element.GetRuntimeId())
    if not 1 <= len(runtime) <= 16 or any(type(x) is not int for x in runtime):
        raise RuntimeError('synthetic_runtime_id_invalid')
    return runtime


def phase(create, uia, target, exchange, name, evidence=None):
    """All element/pattern objects come from this phase's fresh client."""
    if name not in PHASES:
        raise RuntimeError('unknown_synthetic_phase')
    before = exchange('prepare', name)
    if before.get('focus') != target['b']:
        raise RuntimeError('private_focus_baseline_unavailable')
    modern = name != 'legacy_default'
    report = {'phase': name, 'client_kind': 'CUIAutomation8' if modern else 'CUIAutomation',
              'fresh_client': True, 'attempted': False, 'focus_before': before['focus']}
    if evidence is not None:
        evidence.append(report)  # Preserve the attempted state if a COM call raises or times out.
    background = None
    if name == 'modern_no_focus':
        background = BackgroundUIAClient(lambda: create(uia.CUIAutomation8, interface=uia.IUIAutomation2))
        report.update(default_auto_set_focus=int(background.initial_auto_focus), configured_auto_set_focus=0)
        if not background.initial_auto_focus:
            raise RuntimeError('modern_default_focus_flag_not_true')
        element = background.element_from_handle(target['a'])
    else:
        client = create(uia.CUIAutomation8 if modern else uia.CUIAutomation,
                        interface=uia.IUIAutomation2 if modern else uia.IUIAutomation)
    if name == 'modern_default':
        report['default_auto_set_focus'] = int(client.AutoSetFocus)
        if report['default_auto_set_focus'] not in (1, -1):
            raise RuntimeError('modern_default_focus_flag_not_true')
        report['configured_auto_set_focus'] = int(client.AutoSetFocus)
        if report['configured_auto_set_focus'] not in (1, -1):
            raise RuntimeError('auto_set_focus_readback_mismatch')
    if background is None:
        element = client.ElementFromHandle(target['a'])
    runtime = identity(element, target)
    pattern = element.GetCurrentPattern(uia.UIA_ValuePatternId).QueryInterface(uia.IUIAutomationValuePattern)
    if pattern.CurrentIsReadOnly or pattern.CurrentValue != '':
        raise RuntimeError('synthetic_existing_draft_or_readonly')
    if identity(element, target) != runtime:
        raise RuntimeError('synthetic_runtime_id_changed')
    check = exchange('check', name)
    if check.get('focus') != target['b'] or check.get('focus_events'):
        raise RuntimeError('discovery_altered_private_focus')
    if background is not None:
        background.require_background_mode()
    # Claim once before the COM call; failures are not retried.
    report['attempted'] = True
    pattern.SetValue(TEXT[name])
    report['uia_full_readback_equal'] = pattern.CurrentValue == TEXT[name]
    report['identity_stable'] = identity(element, target) == runtime
    after = exchange('complete', name)
    report.update(after)
    report['runtime_id'] = list(runtime)
    return report


def source_qualified(result):
    """No-focus success alone is not a positive control for auto-focus."""
    phases = result.get('phases', [])
    if (result.get('error') or result.get('mta_verified') is not True or len(phases) != 3 or
            [p.get('phase') for p in phases] != list(PHASES)):
        return False
    target = result.get('target', {})
    if not target.get('a') or not target.get('b') or target['a'] == target['b']:
        return False
    for item in phases:
        if (item.get('error') or not all(item.get(key) is True for key in
                ('fresh_client', 'attempted', 'uia_full_readback_equal',
                 'native_full_readback_equal', 'identity_stable')) or
                item.get('focus_before') != target['b'] or item.get('event_overflow')):
            return False
    for item in phases[:2]:
        if item.get('focus') != target['a'] or not any(
                event == [target['a'], 'set'] for event in item.get('focus_events', [])):
            return False
    return (phases[1].get('default_auto_set_focus') in (1, -1) and
            phases[1].get('configured_auto_set_focus') in (1, -1) and
            phases[2].get('default_auto_set_focus') in (1, -1) and
            phases[2].get('configured_auto_set_focus') == 0 and
            phases[2].get('focus') == target['b'] and phases[2].get('focus_events') == [])


def complete_pass(report):
    cleanup = report.get('cleanup', {})
    return bool(source_qualified(report.get('result', {})) and not report.get('error') and
                report.get('source_unchanged') is True and report.get('uia_dll_unchanged') is True and
                report.get('input_checkpoints_unchanged') is True and
                report.get('checkpoint_count', 0) >= 2 and all(cleanup.get(key) is True for key in
                    ('child_exited', 'native_cleanup_confirmed', 'owned_windows_absent',
                     'desktop_closed', 'desktop_absent')) and not cleanup.get('terminated') and
                not cleanup.get('errors'))


def close_import_com(module):
    """Balance comtypes' import on its original thread before changing desktop.

    This isolated canary pins comtypes 1.4.17. Its existing _shutdown callback
    uninitializes COM and marks pointers closed. Run that exact callback early,
    after the worker/references are gone, and unregister it to avoid a double
    uninitialize at process exit. This is not a production client lifecycle API.
    """
    if module.__version__ != COMTYPES_VERSION or not callable(module._shutdown):
        raise RuntimeError('comtypes_cleanup_version_mismatch')
    gc.collect()
    atexit.unregister(module._shutdown)
    module._shutdown()
    return True


def child(pipe, desktop_id):
    """One disposable process: GUI main thread + separate UIA MTA worker."""
    user, kernel = c.WinDLL('user32', use_last_error=True), c.WinDLL('kernel32')
    desktop = original = instance = None
    atom, host, controls, worker, com_module = 0, None, [], None, None
    cleanup, result = {}, {'phases': []}
    commands, answers = queue.Queue(), queue.Queue()
    done = threading.Event()
    state = {'phase': None, 'events': [], 'overflow': False}
    get_tid = call(kernel, 'GetCurrentThreadId', U32, [])
    get_desktop = call(user, 'GetThreadDesktop', PTR, [U32])
    set_desktop = call(user, 'SetThreadDesktop', c.c_int, [PTR])
    get_focus = call(user, 'GetFocus', PTR, [])
    set_focus = call(user, 'SetFocus', PTR, [PTR])
    get_owner = call(user, 'GetWindowThreadProcessId', U32, [PTR, c.POINTER(U32)])

    def guard():
        if desktop_name(user, get_desktop(get_tid())) != desktop_id or input_snapshot()[0] == desktop_id:
            raise RuntimeError('private_desktop_isolation_lost')

    def exchange(action, name):
        commands.put((action, name))
        try:
            reply = answers.get(timeout=2)
        except queue.Empty:
            raise RuntimeError('synthetic_gui_ack_timeout') from None
        if 'error' in reply:
            raise RuntimeError(reply['error'])
        return reply

    def automate(comtypes, create, uia, target):
        previous, initialized = get_desktop(get_tid()), False
        try:
            if not set_desktop(desktop):
                raise RuntimeError('mta_desktop_attach_failed')
            guard()
            comtypes.CoInitializeEx(0)
            initialized = True
            kind, qualifier = c.c_int(), c.c_int()
            hr = call(c.WinDLL('ole32'), 'CoGetApartmentType', c.c_long,
                      [c.POINTER(c.c_int), c.POINTER(c.c_int)])(c.byref(kind), c.byref(qualifier))
            if hr != 0 or kind.value != 1:
                raise RuntimeError('uia_thread_not_mta')
            result['mta_verified'] = True
            for name in PHASES:
                guard()
                phase(create, uia, target, exchange, name, result['phases'])
        except Exception as exc:
            result['error'] = str(exc) if type(exc) is RuntimeError else type(exc).__name__
        finally:
            gc.collect()  # Release local UIA pointers before ending their apartment.
            if initialized:
                comtypes.CoUninitialize()
                cleanup['mta_uninitialized'] = True
            cleanup['mta_desktop_restored'] = bool(set_desktop(previous))
            done.set()

    try:
        original = get_desktop(get_tid())
        desktop = call(user, 'OpenDesktopW', PTR, [c.c_wchar_p, U32, c.c_int, U32])(
            desktop_id, 0, False, 0x83)
        if not desktop or not set_desktop(desktop):
            raise RuntimeError('private_desktop_attach_failed')
        guard()
        # Import initializes this thread; the exact pinned exit callback is run
        # early during cleanup, before restoring its desktop. All UIA calls use MTA.
        sys.coinit_flags = 0
        import comtypes
        com_module = comtypes
        if comtypes.__version__ != COMTYPES_VERSION:
            raise RuntimeError('comtypes_version_mismatch')
        result['comtypes_version'] = comtypes.__version__
        from comtypes.gen import UIAutomationClient as uia
        def create_client(coclass, interface):
            return comtypes.CoCreateInstance(coclass._reg_clsid_, interface=interface,
                                             clsctx=comtypes.CLSCTX_INPROC_SERVER)
        default_proc = call(user, 'DefWindowProcW', c.c_ssize_t, [PTR, U32, U64, c.c_ssize_t])

        def dispatch(hwnd, message, wparam, lparam):
            # Standard Edit notifies its parent; no subclass, global hook or event subscription.
            if message == 0x111 and lparam in controls and state['phase']:
                code = (wparam >> 16) & 0xffff
                if code in (0x100, 0x200):  # EN_SETFOCUS / EN_KILLFOCUS
                    if len(state['events']) < 16:
                        state['events'].append([lparam, 'set' if code == 0x100 else 'kill'])
                    else:
                        state['overflow'] = True
            return default_proc(hwnd, message, wparam, lparam)

        wndproc = CALLBACK(c.c_ssize_t, PTR, U32, U64, c.c_ssize_t)(dispatch)
        instance = call(kernel, 'GetModuleHandleW', PTR, [c.c_wchar_p])(None)
        class_name = 'BridgeAutoFocus-' + uuid.uuid4().hex
        cls = WindowClass(c.sizeof(WindowClass), 0, c.cast(wndproc, PTR), 0, 0, instance,
                          None, None, None, None, class_name, None)
        atom = call(user, 'RegisterClassExW', c.c_uint16, [c.POINTER(WindowClass)])(c.byref(cls))
        if not atom:
            raise RuntimeError('synthetic_class_registration_failed')
        create = call(user, 'CreateWindowExW', PTR, [U32, c.c_wchar_p, c.c_wchar_p, U32,
            c.c_int, c.c_int, c.c_int, c.c_int, PTR, PTR, PTR, PTR])
        # WS_POPUP | WS_VISIBLE, no special focus attributes. This desktop is never switched to.
        host = create(0, class_name, 'M1 synthetic', 0x90000000, 10, 10, 320, 140,
                      None, None, instance, None)
        if not host:
            raise RuntimeError('synthetic_host_creation_failed')
        for index in range(2):
            guard()
            hwnd = create(0, 'Edit', '', 0x50000080, 10, 10 + index * 45, 280, 30,
                          host, PTR(101 + index), instance, None)  # CHILD | VISIBLE | AUTOHSCROLL
            if not hwnd:
                raise RuntimeError('synthetic_edit_creation_failed')
            controls.append(hwnd)
        target = {'pid': os.getpid(), 'tid': get_tid(), 'host': host, 'a': controls[0], 'b': controls[1]}
        for hwnd in (host, *controls):
            owner = U32()
            if get_owner(hwnd, c.byref(owner)) != target['tid'] or owner.value != target['pid']:
                raise RuntimeError('owned_window_identity_mismatch')
        result['target'] = target
        pipe.send({'ready': target})
        worker = threading.Thread(target=automate, args=(comtypes, create_client, uia, target), daemon=True)
        worker.start()
        peek = call(user, 'PeekMessageW', c.c_int, [PTR, PTR, U32, U32, U32])
        translate = call(user, 'TranslateMessage', c.c_int, [PTR])
        dispatch_message = call(user, 'DispatchMessageW', c.c_ssize_t, [PTR])
        set_text = call(user, 'SetWindowTextW', c.c_int, [PTR, c.c_wchar_p])
        get_text = call(user, 'GetWindowTextW', c.c_int, [PTR, c.c_wchar_p, c.c_int])
        get_length = call(user, 'GetWindowTextLengthW', c.c_int, [PTR])
        message, complete_at, pending = c.create_string_buffer(48), None, None
        deadline = time.monotonic() + CHILD_TIMEOUT - 2
        while not done.is_set():
            guard()
            if time.monotonic() >= deadline:
                raise RuntimeError('synthetic_worker_timeout')
            if pipe.poll() and pipe.recv() == 'stop':
                raise RuntimeError('synthetic_parent_stop')
            while peek(message, None, 0, 0, 1):
                translate(message)
                dispatch_message(message)
            if pending and time.monotonic() >= complete_at:
                expected, buffer = TEXT[pending], c.create_unicode_buffer(128)
                length = get_length(controls[0])
                read = get_text(controls[0], buffer, len(buffer))
                answers.put({'focus': get_focus(), 'focus_events': list(state['events']),
                    'event_overflow': state['overflow'],
                    'native_full_readback_equal': length == read == len(expected) and buffer.value == expected})
                pending = None
            try:
                action, name = commands.get(timeout=0.01)
            except queue.Empty:
                continue
            if name not in PHASES or pending:
                raise RuntimeError('synthetic_gui_protocol_mismatch')
            if action == 'prepare':
                state['phase'] = None
                if not set_text(controls[0], ''):
                    raise RuntimeError('synthetic_reset_failed')
                guard()
                set_focus(controls[1])  # Own GUI queue and non-input desktop only.
                state.update(phase=name, events=[], overflow=False)
                answers.put({'focus': get_focus()})
            elif action == 'check' and state['phase'] == name:
                answers.put({'focus': get_focus(), 'focus_events': list(state['events'])})
            elif action == 'complete' and state['phase'] == name:
                pending, complete_at = name, time.monotonic() + 0.25
            else:
                raise RuntimeError('synthetic_gui_protocol_mismatch')
        worker.join(1)
        cleanup['mta_exited'] = not worker.is_alive()
    except Exception as exc:
        result['error'] = str(exc) if type(exc) is RuntimeError else type(exc).__name__
    finally:
        # Never destroy a provider's window while an unconfirmed COM call still runs.
        if worker and worker.is_alive():
            answers.put({'error': 'synthetic_stopping'})
            worker.join(0.3)
        safe_exit = worker is None or not worker.is_alive()
        cleanup['mta_exited'] = safe_exit
        if safe_exit:
            cleanup['window_destroyed'] = not host or bool(call(user, 'DestroyWindow', c.c_int, [PTR])(host))
            cleanup['class_unregistered'] = not atom or bool(call(user, 'UnregisterClassW', c.c_int,
                [c.c_wchar_p, PTR])(class_name, instance))
            if com_module is not None:
                try:
                    cleanup['main_com_uninitialized'] = close_import_com(com_module)
                except Exception:
                    cleanup['main_com_uninitialized'] = False
            cleanup['thread_desktop_restored'] = not original or bool(set_desktop(original))
            cleanup['desktop_closed'] = not desktop or bool(call(user, 'CloseDesktop', c.c_int, [PTR])(desktop))
        try:
            pipe.send({'result': result, 'cleanup': cleanup})
        except (OSError, EOFError):
            pass
        pipe.close()


def cleanup_owned(process, user, desktop, desktop_id, target, native):
    """A process object is the only termination authority; never accepts a PID."""
    result = {'errors': [], 'terminated': False}
    try:
        if process is not None and process.pid:
            process.join(1)
            if process.is_alive():
                result['terminated'] = True
                process.terminate()
                process.join(2)
            result['child_exited'] = not process.is_alive() and process.exitcode == 0
        else:
            result['child_exited'] = False
    except Exception:
        result['errors'].append('owned_child_exit_unconfirmed')
    result['native_cleanup_confirmed'] = all(native.get(key) is True for key in
        ('mta_uninitialized', 'main_com_uninitialized', 'mta_desktop_restored', 'mta_exited', 'window_destroyed',
         'class_unregistered', 'thread_desktop_restored', 'desktop_closed'))
    try:
        is_window = call(user, 'IsWindow', c.c_int, [PTR])
        result['owned_windows_absent'] = bool(target and all(
            not is_window(target[key]) for key in ('host', 'a', 'b')))
    except Exception:
        result['errors'].append('owned_windows_absence_unconfirmed')
    try:
        result['desktop_closed'] = bool(desktop and call(user, 'CloseDesktop', c.c_int, [PTR])(desktop))
    except Exception:
        result['errors'].append('owned_desktop_close_unconfirmed')
    try:
        c.set_last_error(0)
        remaining = call(user, 'OpenDesktopW', PTR, [c.c_wchar_p, U32, c.c_int, U32])(
            desktop_id, 0, False, 1)
        result['desktop_absent'] = not remaining and c.get_last_error() == 2
        if remaining:
            call(user, 'CloseDesktop', c.c_int, [PTR])(remaining)
    except Exception:
        result['errors'].append('owned_desktop_absence_unconfirmed')
    return result


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ('scripts/probe_uia_autofocus.py', 'scripts/test_focus_etw.py',
                         'scripts/inspect_focus_events.py', 'wechat_agent_bridge/adapters/uia_client.py')}


def run():
    report = {'research_only': True, 'live_client_verified': False, 'focus_cause_verified': False,
              'passed': False, 'state': 'source_not_verified', 'checkpoint_count': 0,
              'input_checkpoints_unchanged': True,
              'observation_limit': 'Input foreground and clipboard are sampled, not continuously proven.'}
    if os.name != 'nt' or c.sizeof(PTR) != 8 or c.sizeof(WindowClass) != 80:
        raise RuntimeError('windows_x64_required')
    initial, source = input_snapshot(), fingerprints()
    report.update(os=platform.version(), fingerprints=source)
    dll = Path(os.environ['SystemRoot']) / 'System32/UIAutomationCore.dll'
    report['uia_sha256'] = hashlib.sha256(dll.read_bytes()).hexdigest()
    user, ctx = c.WinDLL('user32', use_last_error=True), mp.get_context('spawn')
    desktop_id = 'BridgeAutoFocusDesktop-' + uuid.uuid4().hex
    desktop = process = parent = remote = None
    target, native = {}, {}
    try:
        desktop = create_private_desktop(user, desktop_id, initial[0])
        parent, remote = ctx.Pipe()
        process = ctx.Process(target=child, args=(remote, desktop_id))
        process.start()
        remote.close()
        deadline = time.monotonic() + CHILD_TIMEOUT
        while time.monotonic() < deadline:
            report['checkpoint_count'] += 1
            if input_snapshot() != initial:
                report['input_checkpoints_unchanged'] = False
                raise RuntimeError('input_desktop_or_user_activity_changed')
            if not parent.poll(0.025):
                if not process.is_alive():
                    raise RuntimeError('synthetic_child_exited_without_result')
                continue
            message = parent.recv()
            if 'ready' in message:
                if target or message['ready'].get('pid') != process.pid:
                    raise RuntimeError('synthetic_child_identity_mismatch')
                target = message['ready']
            elif 'result' in message:
                report['result'], native = message['result'], message.get('cleanup', {})
                break
            else:
                raise RuntimeError('synthetic_child_protocol_mismatch')
        else:
            raise RuntimeError('synthetic_child_hard_timeout')
    except Exception as exc:
        report['error'] = str(exc) if type(exc) is RuntimeError else type(exc).__name__
    finally:
        if parent:
            try:
                parent.send('stop')
            except (OSError, EOFError):
                pass
        report['cleanup'] = cleanup_owned(process, user, desktop, desktop_id, target, native)
        for pipe in (parent, remote):
            if pipe:
                pipe.close()
        report['checkpoint_count'] += 1
        try:
            report['input_checkpoints_unchanged'] &= input_snapshot() == initial
        except Exception:
            report['input_checkpoints_unchanged'] = False
    report['source_unchanged'] = fingerprints() == source
    try:
        report['uia_sha256_after'] = hashlib.sha256(dll.read_bytes()).hexdigest()
        report['uia_dll_unchanged'] = report['uia_sha256_after'] == report['uia_sha256']
    except OSError:
        report['uia_dll_unchanged'] = False
    report['passed'] = complete_pass(report)
    report['state'] = 'synthetic_source_verified' if report['passed'] else 'source_not_verified'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-synthetic', action='store_true')
    args = parser.parse_args(argv)
    if not args.run_synthetic:
        print(json.dumps({'enabled': False, 'passed': False, 'state': 'disabled',
                          'live_client_verified': False, 'focus_cause_verified': False}))
        return 0
    try:
        report = run()
    except Exception as exc:
        report = {'state': 'source_not_verified', 'passed': False, 'live_client_verified': False,
                  'focus_cause_verified': False,
                  'error': str(exc) if type(exc) is RuntimeError else type(exc).__name__}
    destination = ROOT / '.local/live-acceptance/uia-autofocus-synthetic.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
