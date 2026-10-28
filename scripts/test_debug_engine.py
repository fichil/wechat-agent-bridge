"""Opt-in native debugger integration test; creates its own disposable target.

No arbitrary PID argument, desktop access, client DLL, chat data or network call.
Not collected by default CI. Results are only valid for these exact helper files
and the installed DbgEng binary, and do not establish WeChat compatibility.
"""
import ctypes as c
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time
import struct
import argparse
import platform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechat_agent_bridge.adapters.debug_engine import DebugEngine, _call, U32, U64


def target(connection, counter, stop):
    kernel = c.WinDLL("kernel32")
    function = kernel.GetCurrentProcessId
    function.restype = c.c_uint32
    connection.send((os.getpid(), c.cast(function, c.c_void_p).value, kernel.GetCurrentThreadId()))
    while not stop.value:
        function()
        counter.value += 1
        time.sleep(0.01)


def bootstrap(stop):
    until = time.monotonic()+120
    while not stop.value and time.monotonic() < until:
        time.sleep(0.05)


def debugger(connection, pid, address, mode, bootstrap_pid, stage='breakpoint', topology='multi'):
    engine = None
    def checkpoint(name):
        if stage != name: return
        connection.send({'stage_reached': name})
        if mode == 'crash': os._exit(17)
        if mode == 'watchdog':
            connection.send({'awaiting_external_termination':True})
            while True: time.sleep(1)
    try:
        engine = DebugEngine()
        if topology == 'multi':
            engine.prepare(bootstrap_pid)
            engine.attach(pid)
        else:
            # Synthetic control ONLY: this child is disposable. Do not add a
            # direct-first-attach API to the real diagnostic port.
            _call(engine.client,12,(U64,U32,U32),(0,pid,0))
            engine.attached=True
        until = time.monotonic()+5
        while True:
            if engine.wait(100):
                connection.send({"initial_event_target_matches": engine.process_id() == pid})
                if engine.process_id() == pid: break
                engine.resume()
            if time.monotonic() > until:
                connection.send({"timeout_current_target_matches":engine.process_id() == pid, "event_kind":engine.events.kind})
                raise TimeoutError("initial_event_timeout")
        engine.confirm_exit_policy()
        checkpoint('attached')
        if stage == 'attached':
            engine.detach()
            connection.send({'detached':True})
            return
        engine.add_execution_breakpoint(address)
        engine.resume()
        while time.monotonic() < until:
            if not engine.wait(100): continue
            if engine.register("rip") == address:
                stack = engine.stack()
                connection.send({"breakpoint_hit": True, "stack_frames": len(stack), "thread_recorded": engine.thread_id() > 0,
                                 "event_kind":engine.events.kind, "exception":engine.events.exception})
                checkpoint('breakpoint')
                break
            engine.resume()
        else:
            raise TimeoutError("hardware_breakpoint_timeout")
        engine.clear_breakpoints()
        checkpoint('cleared')
        engine.detach()
        connection.send({"detached": True})
        checkpoint('detached')
    except BaseException as exc:
        connection.send({"error": str(exc) if isinstance(exc, (TimeoutError, RuntimeError)) else type(exc).__name__})
    finally:
        if engine:
            try: engine.close()
            except Exception as exc: connection.send({"cleanup_error": str(exc)})


def synthetic_thread_state(pid, tid):
    """Inspect our child's known thread, balancing exactly our suspension.

    No SetThreadContext, extra ResumeThread or real-client PID entrypoint.
    """
    k = c.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.argtypes, k.OpenProcess.restype = [c.c_uint32,c.c_int,c.c_uint32], c.c_void_p
    k.OpenThread.argtypes, k.OpenThread.restype = [c.c_uint32,c.c_int,c.c_uint32], c.c_void_p
    for name in ('CloseHandle','SuspendThread','ResumeThread'):
        getattr(k,name).argtypes = [c.c_void_p]
    k.SuspendThread.restype = k.ResumeThread.restype = c.c_uint32
    k.CheckRemoteDebuggerPresent.argtypes = [c.c_void_p,c.POINTER(c.c_int)]
    k.GetThreadContext.argtypes = [c.c_void_p,c.c_void_p]
    process = k.OpenProcess(0x400,False,pid)
    thread = k.OpenThread(0x0002|0x0008,False,tid)
    suspended = False
    try:
        if not process or not thread: raise RuntimeError('synthetic_inspection_open_failed')
        present = c.c_int()
        if not k.CheckRemoteDebuggerPresent(process,c.byref(present)):
            raise RuntimeError('synthetic_debug_state_failed')
        prior = k.SuspendThread(thread)
        if prior == 0xffffffff: raise RuntimeError('synthetic_suspend_failed')
        suspended = True
        storage = c.create_string_buffer(1232+15)
        address = (c.addressof(storage)+15)&~15
        c.c_uint32.from_address(address+48).value = 0x100010
        if not k.GetThreadContext(thread,address): raise RuntimeError('synthetic_context_failed')
        regs = struct.unpack('<6Q',c.string_at(address+72,48))
        return {'debugger_present':bool(present.value),'prior_suspend_count':prior,
                'debug_registers':list(regs)}
    finally:
        try:
            if suspended and k.ResumeThread(thread) == 0xffffffff:
                raise RuntimeError('synthetic_balance_suspend_failed')
        finally:
            if thread: k.CloseHandle(thread)
            if process: k.CloseHandle(process)


def synthetic_process_state(pid):
    """All threads of this test's own child. A changing set is inconclusive."""
    k=c.WinDLL('kernel32',use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes=[c.c_uint32,c.c_uint32]
    k.CreateToolhelp32Snapshot.restype=c.c_void_p
    k.Thread32First.argtypes=k.Thread32Next.argtypes=[c.c_void_p,c.c_void_p]
    k.CloseHandle.argtypes=[c.c_void_p]
    snapshot=k.CreateToolhelp32Snapshot(4,0)
    if snapshot==c.c_void_p(-1).value: raise RuntimeError('synthetic_thread_snapshot_failed')
    try:
        entry=(c.c_uint32*7)();entry[0]=28
        ok=k.Thread32First(snapshot,c.byref(entry))
        if not ok: raise RuntimeError('synthetic_thread_enumeration_failed')
        tids=[]
        while ok:
            if entry[3]==pid: tids.append(entry[2])
            ok=k.Thread32Next(snapshot,c.byref(entry))
        if c.get_last_error()!=18 or not tids: raise RuntimeError('synthetic_thread_enumeration_incomplete')
    finally: k.CloseHandle(snapshot)
    return {str(t):synthetic_thread_state(pid,t) for t in tids}


def test_mode(ctx, mode, backend='dbgeng', stage='breakpoint', topology='multi'):
    parent, child = ctx.Pipe()
    counter, stop = ctx.Value("L", 0, lock=False), ctx.Value("b", False, lock=False)
    process = ctx.Process(target=target, args=(child, counter, stop))
    anchor = ctx.Process(target=bootstrap, args=(stop,))
    worker = None
    records = []
    try:
        anchor.start(); process.start(); child.close()
        if not parent.poll(5): raise RuntimeError("synthetic_target_timeout")
        pid, address, tid = parent.recv()
        initial_threads=synthetic_process_state(pid)
        initial = initial_threads[str(tid)]
        read, write = ctx.Pipe()
        worker_fn = debugger
        worker_args = (write, pid, address, mode, anchor.pid,stage,topology)
        if backend == 'win32-control':
            from win32_debug_control import debugger as worker_fn
            worker_args = (write,pid,address,mode,anchor.pid,tid)
        worker = ctx.Process(target=worker_fn, args=worker_args)
        worker.start(); write.close()
        until = time.monotonic()+12
        while worker.is_alive() and time.monotonic() < until:
            try: available = read.poll(0.1)
            except (EOFError, OSError): break
            if available:
                try: record = read.recv()
                except EOFError: break
                records.append(record)
                if record.get("awaiting_external_termination"):
                    worker.terminate(); break
        worker.join(1)
        if worker.is_alive():
            worker.terminate(); worker.join(2)
            raise RuntimeError("synthetic_debugger_watchdog_timeout")
        while True:
            try:
                if not read.poll(): break
                records.append(read.recv())
            except (EOFError, OSError): break
        before = counter.value
        recovery_until = time.monotonic()+3
        while process.is_alive() and counter.value <= before and time.monotonic() < recovery_until:
            time.sleep(0.05)
        resumed = process.is_alive() and counter.value > before
        final_threads=synthetic_process_state(pid) if process.is_alive() else {}
        final = final_threads.get(str(tid))
        all_threads_checked = bool(initial_threads and initial_threads.keys()==final_threads.keys())
        all_threads_restored = all_threads_checked and initial_threads==final_threads
        stable = False
        if resumed:
            stable=True
            stable_until=time.monotonic()+2
            previous=counter.value
            while time.monotonic()<stable_until:
                time.sleep(0.1)
                if not process.is_alive() or counter.value<=previous: stable=False; break
                previous=counter.value
        reached = (any(r.get('stage_reached')==stage for r in records) if backend=='dbgeng'
                   else any(r.get('breakpoint_hit') for r in records))
        passed = resumed and stable and reached and all_threads_restored
        if stage!='attached':
            passed &= any(r.get("breakpoint_hit") and (backend=='win32-control' or r.get("stack_frames", 0)>0) for r in records)
        passed &= bool(final and not final['debugger_present'] and final['prior_suspend_count']==initial['prior_suspend_count']
                       and final['debug_registers']==initial['debug_registers'])
        passed &= not any("error" in r or "cleanup_error" in r for r in records)
        if mode == "normal": passed &= any(r.get("detached") for r in records)
        return {"mode": mode, "stage":stage,"topology":topology,"backend":backend,
                "stable_two_seconds":stable,"passed": bool(passed), "target_resumed": resumed,
                "target_alive": process.is_alive(), "target_exitcode":process.exitcode, "records": records,
                "initial_thread":initial, "final_thread":final,
                "all_threads_checked":all_threads_checked,
                "initial_threads":initial_threads,"final_threads":final_threads}
    finally:
        if worker and worker.is_alive(): worker.terminate(); worker.join(2)
        stop.value = True; process.join(2)
        if process.is_alive(): process.terminate(); process.join(2)
        anchor.join(1)
        if anchor.is_alive(): anchor.terminate(); anchor.join(2)
        parent.close()


def helper_fingerprints():
    paths = [Path(__file__), ROOT/"wechat_agent_bridge/adapters/debug_engine.py",
             ROOT/'scripts/win32_debug_control.py',
             ROOT/'wechat_agent_bridge/adapters/focus_diagnostics.py',
             Path(os.environ["SystemRoot"])/"System32/dbgeng.dll"]
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',choices=('dbgeng','win32-control'),default='dbgeng')
    parser.add_argument('--matrix',action='store_true')
    parser.add_argument('--repetitions',type=int,choices=(1,10),default=1)
    args=parser.parse_args()
    ctx = mp.get_context("spawn")
    results = []
    run_fingerprints=helper_fingerprints()
    destination = ROOT/('.local/live-acceptance/debugger-selftest.json' if args.backend=='dbgeng' else '.local/live-acceptance/win32-control.json')
    destination.parent.mkdir(parents=True,exist_ok=True)
    # Starting another run invalidates any previous pass, even if this runner
    # subsequently crashes. The real coordinator remains disabled regardless.
    destination.write_text(json.dumps({'schema':2,'passed':False,'state':'running',
                                      'fingerprints':run_fingerprints}),encoding='utf-8')
    configurations=[(s,t) for s in ('attached','breakpoint','cleared','detached') for t in ('single','multi')] if args.matrix and args.backend=='dbgeng' else [('breakpoint','multi')]
    for stage,topology in configurations:
        for mode in ('normal','crash','watchdog'):
            for repeat in range(args.repetitions):
                result = test_mode(ctx,mode,args.backend,stage,topology)
                result['repeat']=repeat
                results.append(result)
                print(json.dumps({k:result[k] for k in ('backend','stage','topology','mode','repeat',
                    'passed','target_resumed','stable_two_seconds','target_alive','all_threads_checked')}),flush=True)
    report = {"schema":2,"backend":args.backend,"os":platform.version(),
              "passed": bool(results) and all(r["passed"] for r in results) and run_fingerprints==helper_fingerprints(),
              "coverage":"all_target_threads","repetitions":args.repetitions,
              "fingerprints": run_fingerprints, "results": results}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
