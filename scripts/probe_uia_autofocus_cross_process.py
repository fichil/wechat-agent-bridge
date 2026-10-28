"""Opt-in tree canary with separate owned provider and UIA client processes.

Uses the frozen four-phase tree comparison on a private non-input desktop.
The top-level controller owns both children. No external PID/window argument,
WeChat access, debugger, ETW, send, clipboard write or live allowance exists.
"""
import argparse
import ctypes as c
import gc
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import probe_uia_autofocus_tree as tree

base = tree.base
TREE_HASH = '661ba674fb8f980292ab67ff331a3943c98ab349c8003eb16a38897f4af052b4'
DLL_HASH = 'be5037bf46e61566d3e5a83a6c628cebe9035771e4ae741a7b9aa27b5696d989'
CLIENT_SECONDS = 15
_BASE_CHILD = tree._ORIGINAL_CHILD
# Keep ownership handles available if a native exit observation itself fails.
# Entries are only Process objects created by run(), never reopened PID targets.
_RECOVERY_OWNED = []


def require_sources():
    tree.require_base()
    if hashlib.sha256(Path(tree.__file__).read_bytes()).hexdigest() != TREE_HASH:
        raise RuntimeError('frozen_tree_canary_changed')


def fingerprints():
    values = tree.fingerprints()
    values['scripts/probe_uia_autofocus_cross_process.py'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return values


def receive(pipe, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not pipe.poll(remaining):
        raise RuntimeError('cross_process_deadline')
    return pipe.recv()


def created(kernel, handle):
    values = [base.U64() for _ in range(4)]
    fn = base.call(kernel, 'GetProcessTimes', c.c_int, [base.PTR] + [c.POINTER(base.U64)] * 4)
    if not fn(handle, *(c.byref(value) for value in values)):
        raise RuntimeError('owned_creation_time_unavailable')
    return values[0].value


class HeldProcess:
    """Read-only identity and exit observation, never termination authority."""
    def __init__(self, identity):
        self.kernel = c.WinDLL('kernel32', use_last_error=True)
        self.handle = base.call(self.kernel, 'OpenProcess', base.PTR,
            [base.U32, c.c_int, base.U32])(0x101000, False, identity['pid'])
        try:
            if not self.handle or created(self.kernel, self.handle) != identity['created']:
                raise RuntimeError('owned_process_identity_mismatch')
            if self.wait(0) != 258:
                raise RuntimeError('owned_process_not_alive')
        except BaseException:
            self.close()
            raise

    def wait(self, milliseconds):
        return base.call(self.kernel, 'WaitForSingleObject', base.U32,
                         [base.PTR, base.U32])(self.handle, milliseconds)

    def exited_normally(self):
        code = base.U32()
        return bool(self.wait(2000) == 0 and base.call(self.kernel, 'GetExitCodeProcess', c.c_int,
            [base.PTR, c.POINTER(base.U32)])(self.handle, c.byref(code)) and code.value == 0)

    def close(self):
        if self.handle:
            ok = bool(base.call(self.kernel, 'CloseHandle', c.c_int, [base.PTR])(self.handle))
            if ok:
                self.handle = None
            return ok
        return True


def own_identity():
    kernel = c.WinDLL('kernel32')
    handle = base.call(kernel, 'GetCurrentProcess', base.PTR, [])()
    return {'pid': os.getpid(), 'created': created(kernel, handle)}


def target_valid(target, provider_pid):
    keys = ('pid', 'tid', 'host', 'a', 'b')
    return (isinstance(target, dict) and all(type(target.get(k)) is int and target[k] > 0 for k in keys)
            and target['pid'] == provider_pid and len({target[k] for k in ('host', 'a', 'b')}) == 3)


class ExchangeProtocol:
    """Exact phase, nonce and sequence; no text, commands or targets in replies."""
    def __init__(self, nonce, name):
        self.nonce, self.name, self.sequence, self.state = nonce, name, 0, 'new'

    def request(self, action):
        if (action not in ('prepare', 'check', 'complete') or
                (self.state == 'new' and action != 'prepare') or
                (self.state != 'new' and action == 'prepare') or self.state == 'complete' or
                (action == 'complete' and self.state != 'check') or self.sequence >= 32):
            raise RuntimeError('cross_process_exchange_order')
        self.sequence += 1
        self.state = action
        return {'nonce': self.nonce, 'phase': self.name, 'seq': self.sequence, 'action': action}

    def validate(self, message):
        if not isinstance(message, dict) or type(message.get('seq')) is not int:
            raise RuntimeError('cross_process_exchange_schema')
        expected = self.request(message.get('action'))
        if message != expected:
            raise RuntimeError('cross_process_exchange_identity')


def client_main(relay, control, desktop_id, nonce):
    """One remote MTA process for all phases, preserving process-level caches."""
    deadline = time.monotonic() + CLIENT_SECONDS
    done = threading.Event()
    def watchdog():
        while not done.wait(.05):
            if time.monotonic() >= deadline or not mp.parent_process().is_alive():
                os._exit(43)  # Only this owned disposable client.
    threading.Thread(target=watchdog, daemon=True).start()
    user = c.WinDLL('user32', use_last_error=True)
    kernel = c.WinDLL('kernel32')
    tid = base.call(kernel, 'GetCurrentThreadId', base.U32, [])()
    get_desktop = base.call(user, 'GetThreadDesktop', base.PTR, [base.U32])
    set_desktop = base.call(user, 'SetThreadDesktop', c.c_int, [base.PTR])
    original, desktop, com_module, provider = get_desktop(tid), None, None, None
    cleanup, result = {}, {}
    try:
        require_sources()
        desktop = base.call(user, 'OpenDesktopW', base.PTR, [c.c_wchar_p, base.U32, c.c_int, base.U32])(
            desktop_id, 0, False, 0x83)
        if not desktop or not set_desktop(desktop):
            raise RuntimeError('remote_desktop_attach_failed')
        if base.desktop_name(user, get_desktop(tid)) != desktop_id or base.input_snapshot()[0] == desktop_id:
            raise RuntimeError('remote_desktop_not_isolated')
        sys.coinit_flags = 0
        import comtypes
        com_module = comtypes
        from comtypes.gen import UIAutomationClient as uia
        kind, qualifier = c.c_int(), c.c_int()
        hr = base.call(c.WinDLL('ole32'), 'CoGetApartmentType', c.c_long,
            [c.POINTER(c.c_int), c.POINTER(c.c_int)])(c.byref(kind), c.byref(qualifier))
        if hr or kind.value != 1 or comtypes.__version__ != base.COMTYPES_VERSION:
            raise RuntimeError('remote_mta_or_comtypes_unverified')
        own = own_identity()
        control.send({'ready': own, 'mta_verified': True})
        binding = receive(control, deadline)
        if (not isinstance(binding, dict) or binding.get('nonce') != nonce or
                binding.get('provider', {}).get('pid') == own['pid']):
            raise RuntimeError('remote_provider_binding_invalid')
        provider = HeldProcess(binding['provider'])
        control.send({'bound': True})
        def create(coclass, interface):
            return comtypes.CoCreateInstance(coclass._reg_clsid_, interface=interface,
                                             clsctx=comtypes.CLSCTX_INPROC_SERVER)
        for name in tree.PHASES:
            command = receive(relay, deadline)
            target = command.get('target', {}) if isinstance(command, dict) else {}
            if (not isinstance(command, dict) or command.get('nonce') != nonce or command.get('phase') != name
                    or not target_valid(target, binding['provider']['pid']) or provider.wait(0) != 258):
                raise RuntimeError('remote_phase_or_target_mismatch')
            for key in ('host', 'a', 'b'):
                pid = base.U32()
                actual_tid = base.call(user, 'GetWindowThreadProcessId', base.U32,
                    [base.PTR, c.POINTER(base.U32)])(target[key], c.byref(pid))
                if pid.value != target['pid'] or actual_tid != target['tid']:
                    raise RuntimeError('remote_window_owner_mismatch')
            protocol = ExchangeProtocol(nonce, name)
            def exchange(action, phase):
                if phase != name:
                    raise RuntimeError('remote_phase_changed')
                request = protocol.request(action)
                relay.send({'exchange': request})
                response = receive(relay, deadline)
                if not isinstance(response, dict) or response.get('ack') != request or not isinstance(response.get('value'), dict):
                    raise RuntimeError('remote_exchange_ack_mismatch')
                return response['value']
            evidence = []
            try:
                report = tree.tree_phase(create, uia, target, exchange, name, evidence)
            except Exception as exc:
                relay.send({'phase_error': type(exc).__name__, 'phase': name, 'nonce': nonce, 'partial': evidence})
                raise
            report.update(remote_client_pid=own['pid'], remote_client_created=own['created'], remote_mta_verified=True)
            relay.send({'phase_result': report, 'phase': name, 'nonce': nonce})
        if receive(relay, deadline) != {'stop': nonce}:
            raise RuntimeError('remote_stop_protocol_mismatch')
    except BaseException as exc:
        result['error'] = str(exc) if type(exc) is RuntimeError else type(exc).__name__
    finally:
        gc.collect()
        if com_module is not None:
            try:
                cleanup['com_uninitialized'] = base.close_import_com(com_module)
            except Exception:
                cleanup['com_uninitialized'] = False
        cleanup['provider_handle_closed'] = provider.close() if provider else False
        cleanup['desktop_restored'] = bool(original and set_desktop(original))
        cleanup['desktop_closed'] = bool(desktop and base.call(user, 'CloseDesktop', c.c_int, [base.PTR])(desktop))
        result['cleanup'] = cleanup
        for pipe in (relay, control):
            try:
                pipe.send({'client_finished': result, 'nonce': nonce})
            except (OSError, EOFError):
                pass
            pipe.close()
        done.set()


def provider_main(pipe, desktop_id, relay, client_identity, nonce):
    require_sources()
    held = HeldProcess(client_identity)
    deadline = time.monotonic() + CLIENT_SECONDS
    phases = iter(tree.PHASES)
    def phase(_create, _uia, target, exchange, name, evidence=None):
        protocol = ExchangeProtocol(nonce, name)
        try:
            if name != next(phases) or held.wait(0) != 258:
                raise RuntimeError('relay_phase_or_client_mismatch')
            relay.send({'phase': name, 'target': target, 'nonce': nonce})
            while True:
                message = receive(relay, deadline)
                if 'exchange' in message:
                    request = message['exchange']
                    protocol.validate(request)
                    relay.send({'ack': request, 'value': exchange(request['action'], name)})
                elif (message.get('nonce') == nonce and message.get('phase') == name and
                        isinstance(message.get('phase_result'), dict) and protocol.state == 'complete'):
                    report = message['phase_result']
                    if evidence is not None:
                        evidence.append(report)
                    if name == tree.PHASES[-1]:
                        relay.send({'stop': nonce})
                        finished = receive(relay, deadline)
                        if finished.get('nonce') != nonce or 'client_finished' not in finished:
                            raise RuntimeError('remote_cleanup_ack_missing')
                        report['remote_finish'] = finished['client_finished']
                        report['remote_exit_before_provider_cleanup'] = held.exited_normally()
                        if not report['remote_exit_before_provider_cleanup']:
                            raise RuntimeError('remote_exit_unconfirmed')
                        report['relay_client_handle_closed'] = held.close()
                        if not report['relay_client_handle_closed']:
                            raise RuntimeError('relay_client_handle_close_failed')
                    return report
                else:
                    if evidence is not None and isinstance(message.get('partial'), list):
                        evidence.extend(message['partial'])
                    raise RuntimeError('remote_phase_failed')
        except BaseException:
            # Root owns the Process object and kills only its own client first.
            try:
                pipe.send({'abort_client': nonce})
            except (OSError, EOFError):
                pass
            # Keep pumping on the GUI thread while the root or the client's
            # fixed watchdog ends it. This deadline is never reset by messages.
            wait_ms = max(0, min(CLIENT_SECONDS + 1, deadline - time.monotonic() + 1)) * 1000
            if held.handle and held.wait(int(wait_ms)) != 0:
                os._exit(44)  # Abnormal source failure, never successful cleanup evidence.
            raise
    try:
        with tree.replace_attributes(base, phase=phase, PHASES=tree.PHASES,
                TEXT={name: tree.candidate.FIXED_DRAFT for name in tree.PHASES}):
            _BASE_CHILD(pipe, desktop_id)
    finally:
        try:
            if not held.close():
                raise RuntimeError('relay_client_handle_close_failed')
        finally:
            relay.close()


def source_qualified(result):
    if not tree.source_qualified(result):
        return False
    phases = result['phases']
    identities = {(p.get('remote_client_pid'), p.get('remote_client_created')) for p in phases}
    if (len(identities) != 1 or any(type(v) is not int or v <= 0 for v in next(iter(identities))) or
            phases[0]['remote_client_pid'] == result['target']['pid'] or
            not all(p.get('remote_mta_verified') is True for p in phases)):
        return False
    finished = phases[-1].get('remote_finish', {})
    return bool(not finished.get('error') and phases[-1].get('remote_exit_before_provider_cleanup') is True and
        phases[-1].get('relay_client_handle_closed') is True and
        all(finished.get('cleanup', {}).get(k) is True for k in
            ('com_uninitialized', 'provider_handle_closed', 'desktop_restored', 'desktop_closed')))


def complete_pass(report):
    """Pure qualification binds both observed children and both cleanup reports."""
    if not isinstance(report, dict):
        return False
    result, cleanup = report.get('result', {}), report.get('cleanup', {})
    remote, identity = report.get('remote_cleanup', {}), report.get('remote_identity', {})
    provider = report.get('provider_identity', {})
    if (not isinstance(result, dict) or not isinstance(cleanup, dict) or
            not isinstance(remote, dict) or not isinstance(identity, dict) or not isinstance(provider, dict)):
        return False
    for item in (identity, provider):
        if any(type(item.get(k)) is not int or item[k] <= 0 for k in ('pid', 'created')):
            return False
    if identity['pid'] == provider['pid'] or not target_valid(report.get('owned_target'), provider['pid']):
        return False
    if result.get('target') != report['owned_target'] or not source_qualified(result):
        return False
    phases = result['phases']
    if any(p.get('remote_client_pid') != identity['pid'] or
           p.get('remote_client_created') != identity['created'] for p in phases):
        return False
    finished = remote.get('result')
    if (not isinstance(finished, dict) or not finished or finished.get('error') or
            finished != phases[-1].get('remote_finish') or
            not all(finished.get('cleanup', {}).get(k) is True for k in
                    ('com_uninitialized', 'provider_handle_closed', 'desktop_restored', 'desktop_closed'))):
        return False
    return bool(not report.get('error') and not report.get('recovery_required') and
        report.get('source_unchanged') is True and report.get('uia_dll_unchanged') is True and
        report.get('input_checkpoints_unchanged') is True and
        type(report.get('checkpoint_count')) is int and report['checkpoint_count'] >= 2 and
        all(cleanup.get(k) is True for k in ('child_exited', 'native_cleanup_confirmed',
            'owned_windows_absent', 'desktop_closed', 'desktop_absent')) and
        not cleanup.get('terminated') and not cleanup.get('errors') and
        remote.get('exited') is True and type(remote.get('exitcode')) is int and remote['exitcode'] == 0 and
        remote.get('terminated') is False and not remote.get('errors') and
        all(report.get('process_handles_closed', {}).get(k) is True for k in ('client', 'provider')))


def reap_client(process, already_terminated=False):
    """Use only the held child object; failed join gets a real handle recheck."""
    status = {'exited': False, 'exitcode': None, 'terminated': already_terminated, 'errors': []}
    if process is None or not process.pid:
        status['exited'] = True  # No client was created; cannot qualify a source.
        return status
    try:
        process.join(.5)
    except Exception:
        status['errors'].append('remote_join_failed')
    try:
        if process.is_alive():
            status['terminated'] = True
            process.terminate()
            process.join(2)
    except Exception:
        status['errors'].append('remote_termination_or_join_failed')
    try:
        status['exited'], status['exitcode'] = not process.is_alive(), process.exitcode
    except Exception:
        status['errors'].append('remote_exit_observation_failed')
    if not status['exited']:
        try:
            kernel = c.WinDLL('kernel32', use_last_error=True)
            # The sentinel is the original process handle held by multiprocessing.
            # This follow-up never reopens or terminates by a possibly reused PID.
            wait = base.call(kernel, 'WaitForSingleObject', base.U32, [base.PTR, base.U32])
            status['sentinel_rechecked'] = True
            status['sentinel_wait'] = wait(int(process.sentinel), 2000)
            if status['sentinel_wait'] == 0:
                code = base.U32()
                if not base.call(kernel, 'GetExitCodeProcess', c.c_int,
                        [base.PTR, c.POINTER(base.U32)])(int(process.sentinel), c.byref(code)):
                    raise RuntimeError('remote_sentinel_exit_code_failed')
                status.update(exited=True, exitcode=code.value)
        except Exception:
            status['errors'].append('remote_sentinel_recheck_failed')
    return status


def run():
    require_sources()
    if os.name != 'nt' or c.sizeof(base.PTR) != 8:
        raise RuntimeError('windows_x64_required')
    dll = Path(os.environ['SystemRoot']) / 'System32/UIAutomationCore.dll'
    if hashlib.sha256(dll.read_bytes()).hexdigest() != DLL_HASH:
        raise RuntimeError('unreviewed_uia_dll')
    report = {'passed': False, 'research_only': True, 'focus_cause_verified': False,
              'live_client_verified': False, 'live_allowance_touched': False, 'live_retry_authorized': False,
              'input_checkpoints_unchanged': True, 'checkpoint_count': 0, 'uia_sha256': DLL_HASH,
              'os': base.platform.version(), 'observation_limit': 'Input state sampled, not continuously proven.'}
    initial, sources = base.input_snapshot(), fingerprints()
    report['fingerprints'] = sources
    nonce, desktop_id = uuid.uuid4().hex, 'BridgeCrossProcess-' + uuid.uuid4().hex
    ctx, user = mp.get_context('spawn'), c.WinDLL('user32', use_last_error=True)
    desktop = client = provider = None
    pipes, target, native, client_finished = [], {}, {}, {}
    terminated = False
    try:
        desktop = base.create_private_desktop(user, desktop_id, initial[0])
        control, client_control = ctx.Pipe()
        relay_host, relay_client = ctx.Pipe()
        pipes.extend((control, client_control, relay_host, relay_client))
        client = ctx.Process(target=client_main, args=(relay_client, client_control, desktop_id, nonce))
        client.start()
        client_control.close()
        relay_client.close()
        deadline = time.monotonic() + base.CHILD_TIMEOUT
        ready = receive(control, min(deadline, time.monotonic() + 5))
        identity = ready.get('ready', {})
        if identity.get('pid') != client.pid or ready.get('mta_verified') is not True:
            raise RuntimeError('remote_ready_mismatch')
        check = HeldProcess(identity)
        if not check.close():
            raise RuntimeError('remote_identity_handle_close_failed')
        report['remote_identity'] = dict(identity)
        parent, remote = ctx.Pipe()
        pipes.extend((parent, remote))
        provider = ctx.Process(target=provider_main, args=(remote, desktop_id, relay_host, identity, nonce))
        provider.start()
        remote.close()
        relay_host.close()
        # multiprocessing owns the provider handle, so identity cannot bind an arbitrary process.
        kernel = c.WinDLL('kernel32')
        provider_identity = {'pid': provider.pid, 'created': created(kernel, int(provider.sentinel))}
        report['provider_identity'] = dict(provider_identity)
        control.send({'provider': provider_identity, 'nonce': nonce})
        if receive(control, min(deadline, time.monotonic() + 3)) != {'bound': True}:
            raise RuntimeError('remote_bind_ack_missing')
        while time.monotonic() < deadline:
            report['checkpoint_count'] += 1
            if base.input_snapshot() != initial:
                report['input_checkpoints_unchanged'] = False
                raise RuntimeError('input_desktop_or_user_activity_changed')
            if not parent.poll(.02):
                continue
            message = parent.recv()
            if 'ready' in message and not target and target_valid(message['ready'], provider.pid):
                target = message['ready']
                report['owned_target'] = dict(target)
            elif message.get('abort_client') == nonce:
                client.join(.1)
                if client.is_alive():
                    client.terminate()
                    terminated = True
                    client.join(2)
            elif 'result' in message:
                report['result'], native = message['result'], message.get('cleanup', {})
                break
            else:
                raise RuntimeError('provider_protocol_mismatch')
        else:
            raise RuntimeError('cross_process_hard_timeout')
    except Exception as exc:
        report['error'] = str(exc) if type(exc) is RuntimeError else type(exc).__name__
    finally:
        # The remote client is always reaped before cleanup may terminate the provider.
        remote_status = reap_client(client, terminated)
        if 'control' in locals():
            try:
                while control.poll():
                    message = control.recv()
                    if message.get('nonce') == nonce and 'client_finished' in message:
                        client_finished = message['client_finished']
            except (EOFError, OSError):
                pass
        remote_status['result'] = client_finished
        report['remote_cleanup'] = remote_status
        if remote_status['exited']:
            if 'parent' in locals():
                try:
                    parent.send('stop')
                except (EOFError, OSError):
                    pass
            report['cleanup'] = base.cleanup_owned(provider, user, desktop, desktop_id, target, native)
        else:
            # Keep the provider alive while the client may still be inside COM.
            # Both child lifetimes are bounded; retained handles permit explicit
            # caller follow-up instead of declaring an uncertain exit successful.
            _RECOVERY_OWNED.append({'client': client, 'provider': provider, 'desktop_id': desktop_id})
            report['recovery_required'] = True
            report['cleanup'] = {'errors': ['remote_exit_unconfirmed_provider_not_terminated'],
                                 'terminated': False, 'child_exited': False,
                                 'native_cleanup_confirmed': False, 'owned_windows_absent': False,
                                 'desktop_absent': False, 'desktop_closed': False}
            try:
                report['cleanup']['desktop_closed'] = bool(desktop and base.call(user,
                    'CloseDesktop', c.c_int, [base.PTR])(desktop))
            except Exception:
                report['cleanup']['errors'].append('parent_desktop_handle_close_failed')
        report['process_handles_closed'] = {}
        for role, process in (('client', client), ('provider', provider)):
            closed = False
            try:
                if process is None:
                    closed = True
                elif not process.pid or not process.is_alive():
                    process.close()
                    closed = True
            except Exception:
                report['cleanup']['errors'].append(role + '_process_handle_close_failed')
            report['process_handles_closed'][role] = closed
        for pipe in pipes:
            try:
                pipe.close()
            except Exception:
                report['cleanup']['errors'].append('controller_pipe_close_failed')
        report['checkpoint_count'] += 1
        try:
            report['input_checkpoints_unchanged'] &= base.input_snapshot() == initial
        except Exception:
            report['input_checkpoints_unchanged'] = False
    report['source_unchanged'] = fingerprints() == sources
    report['uia_dll_unchanged'] = hashlib.sha256(dll.read_bytes()).hexdigest() == DLL_HASH
    report['passed'] = complete_pass(report)
    report['state'] = 'synthetic_cross_process_verified' if report['passed'] else 'source_not_verified'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-synthetic', action='store_true')
    args = parser.parse_args(argv)
    if not args.run_synthetic:
        print(json.dumps({'state': 'disabled', 'passed': False, 'live_client_verified': False}))
        return 0
    try:
        report = run()
    except Exception as exc:
        report = {'passed': False, 'state': 'source_not_verified', 'error': type(exc).__name__, 'live_client_verified': False}
    path = ROOT / '.local/live-acceptance' / ('uia-autofocus-cross-process-' + uuid.uuid4().hex + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({'path': str(path), 'passed': report['passed'], 'state': report['state'], 'error': report.get('error')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
