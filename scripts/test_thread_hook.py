"""Explicit synthetic-only thread hook experiment on an invisible desktop.

Creates every target itself. No PID/HWND arguments, WeChat access, clipboard
write, keyboard/mouse input, paid API or functional client patch is supported.
"""
import argparse
import ctypes as c
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_thread_hook import OUT, BOUND_OUT, SEND_OUT

def call(dll,name,result,arguments):
    function=getattr(dll,name)
    function.restype,function.argtypes=result,arguments
    return function


def create_private_desktop(*args):
    # Native-only imports must not change sys.path during offline discovery.
    from scripts.test_focus_etw import create_private_desktop as create
    return create(*args)


def input_snapshot():
    from scripts.test_focus_etw import input_snapshot as snapshot
    return snapshot()

U32, U64, I32, PTR = c.c_uint32, c.c_uint64, c.c_int32, c.c_void_p
LONG_FIELDS = ('phase stop request consumed entries active status owner_ready owner_stop owner_unhooked '
               'owner_drained owner_error target_ready target_stop heartbeat target_calls target_thread generation '
               'object_alive expected_generation block_before_dispatch callback_arrived release_callback helper_loaded '
               'wake wake_done wake_result block_inside_call operation_entered release_operation '
               'block_before_install install_reached release_install').split()


class Contract(c.Structure):
    _fields_ = [('magic', U64), ('version', U32), ('bytes', U32)] + [
        (x, U32) for x in ('controller_pid', 'owner_pid', 'target_pid', 'target_tid')] + [
        (x, U64) for x in ('controller_created', 'owner_created', 'target_created', 'hwnd', 'nonce', 'deadline')] + [
        (x, I32) for x in LONG_FIELDS]


CASES = ('normal', 'duplicate', 'paused', 'expired', 'target_identity', 'owner_identity',
         'stale_object', 'destroyed_object', 'cancel_pending', 'owner_crash_pending',
         'controller_identity_pending', 'transport_timeout_pending', 'owner_crash_inflight', 'cancel_inflight',
         'controller_crash_pending', 'owner_crash_before_install', 'owner_crash_idle', 'target_exit')


def harness_sources():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ('scripts/test_thread_hook.py','scripts/test_focus_etw.py')}


def validate_build(*, bound_inspector=False, bound_send=False):
    bound_inspector=bound_inspector or bound_send
    directory = SEND_OUT if bound_send else BOUND_OUT if bound_inspector else OUT
    manifest = json.loads((directory / 'build.json').read_text(encoding='utf-8'))
    if bound_inspector:
        if manifest.get('bound_inspector') is not True or manifest.get('real_send_implemented') is not bound_send:
            raise RuntimeError('readonly_bound_build_required')
    elif manifest.get('synthetic_only') is not True:
        raise RuntimeError('synthetic_build_required')
    sources = {'thread_hook.cpp', 'thread_hook_synthetic.cpp', 'thread_hook_contract.h'}
    binaries={'bridge_thread_hook.dll','bridge_hook_synthetic.exe'}
    if bound_inspector:
        sources.update(('bound_send_inspector.h','bound_guard_synthetic.cpp'))
        binaries.add('bridge_bound_guards.exe')
    if (len(manifest.get('binaries', [])) != len(binaries) or
            {x.get('file') for x in manifest['binaries']} != binaries or
            set(manifest.get('sources', {})) != sources):
        raise RuntimeError('synthetic_manifest_incomplete')
    for item in manifest['binaries']:
        if item['file'] not in binaries:
            raise RuntimeError('unrecognized_synthetic_binary')
        if hashlib.sha256((directory / item['file']).read_bytes()).hexdigest() != item['sha256']:
            raise RuntimeError('synthetic_binary_changed')
    for name, expected in manifest['sources'].items():
        if Path(name).name != name or hashlib.sha256((ROOT / 'scripts/native' / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('synthetic_source_changed')
    return manifest


class Experiment:
    def __init__(self):
        if os.name != 'nt' or c.sizeof(PTR) != 8 or c.sizeof(Contract) != 216:
            raise RuntimeError('synthetic_windows_x64_required')
        self.k, self.u = c.WinDLL('kernel32', use_last_error=True), c.WinDLL('user32', use_last_error=True)
        self.children, self.mapping, self.view, self.desktop, self.s = [], None, None, None, None
        self.initial = input_snapshot()
        self.report = dict(cleanup={}, samples=0, focus_changed=False, synthetic_only=True)
        self.name = 'BridgeHookSynthetic-' + uuid.uuid4().hex

    def check(self):
        self.report['samples'] += 1
        if input_snapshot() != self.initial:
            self.report['focus_changed'] = True
            raise RuntimeError('current_input_environment_changed')

    def wait(self, predicate, seconds=3):
        end = time.monotonic() + seconds
        while not predicate():
            self.check()
            if time.monotonic() >= end:
                raise RuntimeError('synthetic_condition_timeout')
            time.sleep(.01)
        self.check()

    def open(self, *, before_install=False):
        self.desktop = create_private_desktop(self.u, self.name, self.initial[0])
        nonce = int.from_bytes(os.urandom(8), 'little') or 1
        map_name = f'Local\\BridgeM1ThreadHook-{os.getpid()}-{nonce:016x}'
        self.map_name = map_name
        self.mapping = call(self.k, 'CreateFileMappingW', PTR, [PTR, PTR, U32, U32, U32, c.c_wchar_p])(
            PTR(-1), None, 4, 0, c.sizeof(Contract), map_name)
        if not self.mapping or c.get_last_error() == 183:
            raise RuntimeError('synthetic_mapping_creation_failed')
        self.view = call(self.k, 'MapViewOfFile', PTR, [PTR, U32, U32, U32, c.c_size_t])(
            self.mapping, 0xF001F, 0, 0, c.sizeof(Contract))
        if not self.view:
            raise RuntimeError('synthetic_mapping_failed')
        self.s = Contract.from_address(self.view)
        self.s.magic, self.s.version, self.s.bytes = 0x4252494447454831, 1, c.sizeof(Contract)
        self.s.controller_pid, self.s.nonce = os.getpid(), nonce
        times = (U64(), U64(), U64(), U64())
        if not call(self.k, 'GetProcessTimes', I32, [PTR, PTR, PTR, PTR, PTR])(
                PTR(-1), *(c.byref(x) for x in times)):
            raise RuntimeError('synthetic_controller_identity_failed')
        self.s.controller_created = times[0].value
        self.target = self.start('target', map_name)
        self.wait(lambda: self.s.target_ready == 1 or self.target.poll() is not None)
        if self.target.poll() is not None or self.s.target_pid != self.target.pid:
            raise RuntimeError('synthetic_target_not_ready')
        self.s.block_before_install = int(before_install)
        self.owner = self.start('owner', map_name)
        self.wait(lambda: (self.s.install_reached == 1 if before_install else self.s.owner_ready == 1)
                  or self.owner.poll() is not None)
        if self.owner.poll() is not None or self.s.owner_pid != self.owner.pid:
            raise RuntimeError('synthetic_owner_not_ready')
        self.s.expected_generation = 1

    def start(self, role, map_name):
        child = subprocess.Popen([str(OUT / 'bridge_hook_synthetic.exe'), role, map_name, self.name,
                                  str(OUT / 'bridge_thread_hook.dll')], creationflags=0x08000000,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append((role, child))
        return child

    def request(self, sequence=1, deadline_ms=1500):
        self.s.request = sequence
        self.s.deadline = call(self.k, 'GetTickCount64', U64, [])() + deadline_ms
        self.s.phase = 1
        self.s.wake += 1

    def wake_after_removal(self):
        # A desktop-bound sender proves the message was actually delivered;
        # failure to send across desktops is not evidence of hook removal.
        probe = self.start('probe', self.map_name)
        self.wait(lambda: probe.poll() is not None)
        if probe.returncode != 0:
            raise RuntimeError('post_unhook_probe_not_delivered')

    def finish_owner(self):
        self.s.stop = 1
        self.s.owner_stop = 1
        self.wait(lambda: self.owner.poll() is not None)
        if self.owner.returncode != 0 or not self.s.owner_unhooked or not self.s.owner_drained:
            raise RuntimeError('synthetic_unhook_not_confirmed')

    def close(self):
        if self.s:
            self.s.stop = self.s.owner_stop = self.s.target_stop = 1
            self.s.release_callback = 1
            self.s.release_operation = 1
            self.s.release_install = 1
        exits = []
        for role, child in reversed(self.children):
            forced = False
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                forced = True
                child.terminate()  # Popen's held handle identifies only our child.
                child.wait(timeout=2)
            exits.append(dict(role=role, exitcode=child.returncode, forced=forced))
            if isinstance(child, AttachedChild): child.close()
        self.report['cleanup']['children'] = exits
        self.s = None
        if self.view:
            self.report['cleanup']['unmapped'] = bool(call(self.k, 'UnmapViewOfFile', I32, [PTR])(self.view))
        if self.mapping:
            self.report['cleanup']['mapping_closed'] = bool(call(self.k, 'CloseHandle', I32, [PTR])(self.mapping))
        if self.desktop:
            self.report['cleanup']['desktop_closed'] = bool(call(self.u, 'CloseDesktop', I32, [PTR])(self.desktop))


def run_case(case):
    if case == 'controller_crash_pending':
        return run_controller_failure()
    if case in ('owner_crash_before_install', 'owner_crash_idle', 'target_exit'):
        return run_lifecycle_case(case)
    exp = Experiment()
    report = exp.report
    report['case'] = case
    try:
        exp.open()
        s = exp.s
        target_created = s.target_created
        if case == 'paused': s.stop = 1
        if case == 'target_identity': s.target_created += 1
        if case == 'owner_identity': s.owner_created += 1
        if case == 'stale_object': s.expected_generation = 2
        if case == 'destroyed_object': s.object_alive = 0
        if case in ('cancel_pending', 'owner_crash_pending', 'controller_identity_pending', 'transport_timeout_pending'):
            s.block_before_dispatch = 1
        if case in ('owner_crash_inflight', 'cancel_inflight'): s.block_inside_call = 1
        exp.request(deadline_ms=0 if case == 'expired' else 1500)
        if s.block_before_dispatch:
            exp.wait(lambda: s.callback_arrived == 1)
            if case == 'cancel_pending': s.stop = 1
            elif case == 'controller_identity_pending': s.controller_created += 1
            elif case == 'transport_timeout_pending':
                exp.wait(lambda: s.wake_done == s.wake)
                if s.wake_result != 0 or s.phase != 2: raise RuntimeError('transport_timeout_not_observed')
                s.stop = 1
            elif case == 'owner_crash_pending':
                exp.owner.terminate()
                exp.owner.wait(timeout=2)
        if s.block_inside_call:
            exp.wait(lambda: s.operation_entered == 1)
            if case == 'owner_crash_inflight':
                exp.owner.terminate(); exp.owner.wait(timeout=2)
            else: s.stop = 1
            s.release_operation = 1
        exp.wait(lambda: s.phase in (3, 4))
        if case == 'duplicate':
            exp.wait(lambda: s.wake_done == s.wake)
            exp.request(sequence=1)
            exp.wait(lambda: s.phase == 4)
        expected = {'normal': (1, 1), 'duplicate': (1, 5), 'paused': (0, 3), 'expired': (0, 4),
                    'target_identity': (0, 2), 'owner_identity': (0, 2), 'stale_object': (0, 6),
                    'destroyed_object': (0, 6), 'cancel_pending': (0, 3), 'owner_crash_pending': (0, 2),
                    'controller_identity_pending': (0, 2), 'transport_timeout_pending': (0, 3),
                    'owner_crash_inflight': (1, 1), 'cancel_inflight': (1, 1)}[case]
        report.update(calls=s.target_calls, status=s.status, gui_thread_match=s.target_thread == s.target_tid,
                      entries=s.entries, target_alive=exp.target.poll() is None)
        if (s.target_calls, s.status) != expected or (s.target_calls and s.target_thread != s.target_tid):
            raise RuntimeError('synthetic_semantic_verdict_failed')
        if not case.startswith('owner_crash_'): exp.finish_owner()
        exp.wait(lambda: s.active == 0)
        s.target_created = target_created  # Restore only our synthetic fault field for the independent sender.
        before, heartbeat = s.entries, s.heartbeat
        s.stop = 0; s.request += 1; s.phase = 1
        exp.wake_after_removal()
        exp.wait(lambda: s.heartbeat >= heartbeat + 2)
        report['post_unhook_no_callback'] = s.entries == before and s.target_calls == expected[0]
        report['helper_unloaded'] = s.helper_loaded == 0
        if not report['post_unhook_no_callback'] or not report['helper_unloaded']:
            raise RuntimeError('synthetic_hook_recovery_unverified')
        report['passed'] = True
    except Exception as exc:
        report.update(passed=False, error=str(exc))
    finally:
        exp.close()
    if not cleanup_complete(report['cleanup'], case):
        report['passed'] = False
    return report


def cleanup_complete(cleanup, case):
    children = cleanup.get('children', [])
    required = {'owner', 'target'} if case == 'target_exit' else {'owner', 'target', 'probe'}
    return (all(cleanup.get(key) is True for key in ('unmapped', 'mapping_closed', 'desktop_closed')) and
            required.issubset({x['role'] for x in children}) and
            all(not x['forced'] and (x['exitcode'] != 0 if x['role'] == 'owner' and case.startswith('owner_crash_')
                                    else x['exitcode'] == 0) for x in children))


def run_lifecycle_case(case):
    exp = Experiment()
    report = exp.report
    report['case'] = case
    try:
        exp.open(before_install=case == 'owner_crash_before_install')
        s = exp.s
        if case == 'target_exit':
            s.target_stop = 1
            exp.wait(lambda: exp.target.poll() is not None)
            exp.finish_owner()
            report.update(target_exitcode=exp.target.returncode, hook_inactive_reason=s.owner_unhooked,
                          calls=s.target_calls, passed=exp.target.returncode == 0 and s.active == 0 and s.target_calls == 0)
        else:
            report['hook_installed_before_exit'] = bool(s.owner_ready)
            exp.owner.terminate(); exp.owner.wait(timeout=2)
            s.request=1; s.phase=1
            before, heartbeat = s.entries, s.heartbeat
            exp.wake_after_removal()
            exp.wait(lambda: s.heartbeat >= heartbeat+2)
            report.update(calls=s.target_calls,target_alive=exp.target.poll() is None,
                          post_unhook_no_callback=s.entries == before,helper_unloaded=s.helper_loaded == 0)
            report['passed'] = (report['target_alive'] and report['post_unhook_no_callback'] and
                                report['helper_unloaded'] and s.target_calls == 0 and
                                report['hook_installed_before_exit'] == (case == 'owner_crash_idle'))
    except Exception as exc:
        report.update(passed=False,error=str(exc))
    finally: exp.close()
    if not cleanup_complete(report['cleanup'],case): report['passed']=False
    return report


class AttachedChild:
    """A held handle to a grandchild created by our own controller, no PID CLI."""
    def __init__(self, kernel, pid, created):
        self.k, self.pid, self.returncode = kernel, pid, None
        self.handle = call(kernel, 'OpenProcess', PTR, [U32,I32,U32])(0x100000 | 0x1000 | 1, False, pid)
        times = (U64(), U64(), U64(), U64())
        if not self.handle or not call(kernel, 'GetProcessTimes', I32, [PTR,PTR,PTR,PTR,PTR])(
                self.handle, *(c.byref(x) for x in times)) or times[0].value != created:
            if self.handle: call(kernel, 'CloseHandle', I32, [PTR])(self.handle)
            raise RuntimeError('synthetic_grandchild_identity_changed')

    def poll(self):
        code = U32()
        if not call(self.k, 'GetExitCodeProcess', I32, [PTR,PTR])(self.handle,c.byref(code)):
            raise RuntimeError('synthetic_grandchild_status_failed')
        self.returncode = None if code.value == 259 else code.value
        return self.returncode

    def wait(self, timeout):
        status = call(self.k, 'WaitForSingleObject', U32, [PTR,U32])(self.handle,int(timeout*1000))
        if status == 258: raise subprocess.TimeoutExpired('synthetic_owned_grandchild', timeout)
        if status != 0: raise RuntimeError('synthetic_grandchild_wait_failed')
        return self.poll()

    def terminate(self):
        if not call(self.k, 'TerminateProcess', I32, [PTR,U32])(self.handle,88):
            raise RuntimeError('synthetic_owned_cleanup_failed')

    def close(self):
        if self.handle: call(self.k, 'CloseHandle', I32, [PTR])(self.handle)
        self.handle = None


def controller_child(pipe):
    experiment = Experiment()
    try:
        experiment.open()
        s = experiment.s
        s.block_before_dispatch = 1
        experiment.request(deadline_ms=2500)
        experiment.wait(lambda: s.callback_arrived == 1)
        pipe.send(dict(map_name=experiment.map_name, desktop=experiment.name,
                       target_pid=s.target_pid, target_created=s.target_created,
                       owner_pid=s.owner_pid, owner_created=s.owner_created))
        pipe.recv()  # The guardian deliberately kills this owned controller.
    except Exception as exc:
        try: pipe.send(dict(error=str(exc)))
        except (OSError, EOFError): pass
    finally:
        experiment.close()
        pipe.close()


def run_controller_failure():
    exp = Experiment()
    report = exp.report
    report['case'] = 'controller_crash_pending'
    context = mp.get_context('spawn')
    parent, child = context.Pipe()
    controller = context.Process(target=controller_child, args=(child,))
    try:
        controller.start(); child.close()
        if not parent.poll(5): raise RuntimeError('synthetic_controller_setup_timeout')
        info = parent.recv()
        if 'error' in info: raise RuntimeError(info['error'])
        exp.map_name, exp.name = info['map_name'], info['desktop']
        exp.desktop = call(exp.u, 'OpenDesktopW', PTR, [c.c_wchar_p,U32,I32,U32])(exp.name,0,False,0x8b)
        exp.mapping = call(exp.k, 'OpenFileMappingW', PTR, [U32,I32,c.c_wchar_p])(0xF001F,False,exp.map_name)
        exp.view = call(exp.k, 'MapViewOfFile', PTR, [PTR,U32,U32,U32,c.c_size_t])(
            exp.mapping,0xF001F,0,0,c.sizeof(Contract)) if exp.mapping else None
        if not exp.desktop or not exp.view: raise RuntimeError('synthetic_guardian_attachment_failed')
        exp.s = Contract.from_address(exp.view)
        s = exp.s
        if s.controller_pid != controller.pid or s.active != 1 or s.target_calls:
            raise RuntimeError('synthetic_controller_barrier_mismatch')
        exp.target = AttachedChild(exp.k,info['target_pid'],info['target_created'])
        exp.children.append(('target',exp.target))
        exp.owner = AttachedChild(exp.k,info['owner_pid'],info['owner_created'])
        exp.children.append(('owner',exp.owner))
        controller.terminate(); controller.join(2)
        if controller.is_alive(): raise RuntimeError('synthetic_controller_not_terminated')
        report['controller_exitcode'] = controller.exitcode
        exp.wait(lambda: s.phase == 4 and s.active == 0 and exp.owner.poll() is not None)
        if s.status != 2 or s.target_calls or exp.owner.returncode != 0 or not s.owner_unhooked or not s.owner_drained:
            raise RuntimeError('synthetic_controller_recovery_failed')
        before, heartbeat = s.entries, s.heartbeat
        s.stop=0; s.phase=1; s.request+=1
        exp.wake_after_removal()
        exp.wait(lambda: s.heartbeat >= heartbeat+2)
        report.update(calls=s.target_calls,status=s.status,target_alive=exp.target.poll() is None,
                      post_unhook_no_callback=s.entries == before,helper_unloaded=s.helper_loaded == 0)
        report['passed'] = (report['target_alive'] and report['post_unhook_no_callback'] and
                            report['helper_unloaded'] and controller.exitcode != 0 and s.target_calls == 0)
    except Exception as exc:
        report.update(passed=False,error=str(exc))
    finally:
        if controller.is_alive():
            try: parent.send('cleanup')
            except (OSError,EOFError): pass
            controller.join(4)
        if controller.is_alive(): controller.terminate(); controller.join(2)
        exp.close(); parent.close(); child.close()
        if not controller.is_alive(): controller.close()
    if not cleanup_complete(report['cleanup'], 'controller_crash_pending'):
        report['passed'] = False
    return report


def main(argv=None):
    global OUT
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--run-synthetic', action='store_true')
    parser.add_argument('--bound-inspector', action='store_true', help='Exercise the read-only candidate build on self-created targets only.')
    parser.add_argument('--bound-send',action='store_true',help='Exercise send candidate transport on self-created targets only; never WeChat.')
    parser.add_argument('--case', choices=CASES, action='append')
    args = parser.parse_args(argv)
    if not args.run_synthetic:
        print(json.dumps(dict(state='disabled', live_enabled=False)))
        return 0
    if args.bound_inspector and args.bound_send: parser.error('select_one_candidate_build')
    build = validate_build(bound_inspector=args.bound_inspector,bound_send=args.bound_send)
    if args.bound_inspector or args.bound_send: OUT = SEND_OUT if args.bound_send else BOUND_OUT
    report = dict(build=build, cases=[], live_eligible=False, synthetic_only=True,harness_sources=harness_sources())
    if args.bound_inspector or args.bound_send:
        fixture=subprocess.run([str(OUT/'bridge_bound_guards.exe')],capture_output=True,text=True,
                               creationflags=0x08000000,timeout=5)
        if fixture.returncode: raise RuntimeError('bound_guard_fixture_failed_'+str(fixture.returncode))
        report['guard_fixture']=json.loads(fixture.stdout)
        if report['guard_fixture'].get('passed') is not True: raise RuntimeError('bound_guard_fixture_rejected')
    for case in args.case or CASES:
        result = run_case(case)
        report['cases'].append(result)
        if not result['passed']: break
    report['passed'] = all(x['passed'] for x in report['cases'])
    report['harness_unchanged']=harness_sources()==report['harness_sources']
    if not report['harness_unchanged']: report['passed']=False
    path = ROOT / '.local/live-acceptance' / ('thread-hook-synthetic-' + uuid.uuid4().hex + '.json')
    with path.open('x', encoding='utf-8') as stream: json.dump(report, stream, indent=2)
    print(json.dumps(dict(path=str(path), passed=report['passed'], cases=report['cases'])))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
