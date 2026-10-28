"""Opt-in hidden self-created STATIC window ABI test; never contacts WeChat."""
import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def owned_worker(pipe, with_numeric=False):
    sys.coinit_flags = 0
    hwnd = None
    report = dict(kind='owned_window_oleacc', contains_real_client=False, passed=False, cleanup_errors=[])
    user = c.WinDLL('user32', use_last_error=True)
    user.CreateWindowExW.argtypes = [w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,c.c_int,c.c_int,c.c_int,c.c_int,
                                    w.HWND,w.HMENU,w.HINSTANCE,c.c_void_p]
    user.CreateWindowExW.restype = w.HWND
    user.DestroyWindow.argtypes, user.DestroyWindow.restype = [w.HWND], w.BOOL
    user.IsWindow.argtypes, user.IsWindow.restype = [w.HWND], w.BOOL
    user.IsWindowVisible.argtypes, user.IsWindowVisible.restype = [w.HWND], w.BOOL
    user.GetForegroundWindow.restype = w.HWND
    before = user.GetForegroundWindow()
    try:
        from scripts.window_oleacc import WindowOleaccPort, inspect_window
        hwnd = user.CreateWindowExW(0, 'STATIC', 'M1 owned OLEACC fixture', 0x80000000,
                                    20000,20000,320,200,None,None,None,None)
        if not hwnd:
            raise RuntimeError('owned_window_create_failed')
        report['hidden_before'] = not bool(user.IsWindowVisible(hwnd))
        report['probe'] = inspect_window(WindowOleaccPort(dict(pid=os.getpid(), hwnd=hwnd)), hwnd)
        if with_numeric:
            from scripts.window_oleacc_hit import HitPort, scoped, resolve_hit
            port = HitPort(dict(pid=os.getpid(), hwnd=hwnd))
            code, obj = port.create(hwnd)
            if code != 0 or not obj or scoped(port,obj) != hwnd:
                raise RuntimeError('owned_numeric_scope_failed')
            if port.identity(obj) <= 0 or port.identity(obj) != port.identity(obj):
                raise RuntimeError('owned_com_identity_unstable')
            role, loc = port.numeric(obj,0)
            if type(role) is not int or len(loc)!=4 or any(type(v) is not int for v in loc):
                raise RuntimeError('owned_numeric_abi_failed')
            # Hidden own window may legitimately return empty. This validates
            # only native marshaling, not visible avatar hit behavior.
            value = port.hit(obj,(20010,20010))
            hit_obj, child, kind = resolve_hit(port,obj,value)
            if hit_obj is not None:
                scoped(port,hit_obj)
            report['numeric_abi'] = dict(role=role, location=list(loc), hit_kind=kind,
                                         visible_hit_verified=False, action_executed=False)
        report['hidden_after'] = not bool(user.IsWindowVisible(hwnd))
        report['foreground_unchanged'] = user.GetForegroundWindow() == before
        report['passed'] = (report['probe']['status']=='scoped_interface_available' and
                            report['hidden_before'] and report['hidden_after'] and report['foreground_unchanged'])
    except Exception as exc:
        report['reason'] = type(exc).__name__
    finally:
        if hwnd:
            if not user.DestroyWindow(hwnd):
                report['cleanup_errors'].append('owned_destroy_failed')
            report['owned_window_absent'] = not bool(user.IsWindow(hwnd))
        else:
            report['owned_window_absent'] = True
        report['passed'] = report['passed'] and not report['cleanup_errors'] and report['owned_window_absent']
        pipe.send(report)
        pipe.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--run-synthetic', action='store_true')
    parser.add_argument('--with-numeric', action='store_true')
    args = parser.parse_args(argv)
    if not args.run_synthetic:
        print(json.dumps(dict(state='disabled', contains_real_client=False)))
        return 0
    ctx = mp.get_context('spawn')
    parent, child = ctx.Pipe(False)
    process = ctx.Process(target=owned_worker, args=(child,args.with_numeric))
    process.start()
    child.close()
    report = dict(kind='owned_window_oleacc', passed=False, reason='owned_worker_timeout', contains_real_client=False)
    try:
        if parent.poll(15):
            try:
                report = parent.recv()
            except EOFError:
                report['reason'] = 'owned_worker_pipe_closed'
        process.join(1)
        if process.is_alive():
            process.terminate()
            process.join(2)
            report.update(passed=False, terminated=True)
        report['child_exited'] = not process.is_alive()
        report['passed'] = report.get('passed') is True and process.exitcode==0 and report['child_exited']
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
            process.join(2)
            report.update(passed=False, terminated=True)
        if not process.is_alive():
            process.close()
    report['source_sha256'] = hashlib.sha256((ROOT/'scripts/window_oleacc.py').read_bytes()).hexdigest()
    report['harness_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if args.with_numeric:
        report['numeric_source_sha256'] = hashlib.sha256((ROOT/'scripts/window_oleacc_hit.py').read_bytes()).hexdigest()
    path = ROOT/'.local/live-acceptance'/('owned-window-oleacc-'+uuid.uuid4().hex+'.json')
    raw = (json.dumps(report,indent=2)+'\n').encode()
    with path.open('xb') as stream: stream.write(raw)
    print(json.dumps(dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), report=report)))
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
