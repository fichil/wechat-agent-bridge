"""Own-child-only recovery experiment; never a live diagnostic admission report.

The only target PID comes from a process this script creates. A second DbgEng
process attempts documented attach/detach after a deliberately killed engine.
No manual suspend-count correction, register writes, UI or network access.
"""
import argparse
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_debug_engine import (
    DebugEngine, U32, U64, _call, bootstrap, debugger, helper_fingerprints,
    synthetic_process_state, target,
)


def rescue(connection, pid, bootstrap_pid, flags):
    engine = None
    try:
        engine = DebugEngine()
        engine.prepare(bootstrap_pid)
        _call(engine.client, 12, (U64, U32, U32), (0, pid, flags))
        engine.user_target_attached = True
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if engine.wait(100):
                if engine.process_id() == pid:
                    connection.send({'rescue_initial_event': True,
                                     'event_kind': engine.events.kind})
                    engine.confirm_exit_policy()
                    engine.detach()
                    connection.send({'rescue_detached': True})
                    return
                engine.resume()
        raise TimeoutError('rescue_initial_event_timeout')
    except BaseException as exc:
        connection.send({'rescue_error': str(exc) if isinstance(exc, (RuntimeError, TimeoutError))
                         else type(exc).__name__})
    finally:
        if engine:
            try:
                engine.close()
            except Exception as exc:
                connection.send({'rescue_cleanup_error': str(exc)})


def collect(worker, connection, timeout=12):
    records = []
    deadline = time.monotonic() + timeout
    while worker.is_alive() and time.monotonic() < deadline:
        try:
            if connection.poll(.1):
                record = connection.recv()
                records.append(record)
                if record.get('awaiting_external_termination'):
                    worker.terminate()
                    break
        except EOFError:
            break
    worker.join(1)
    if worker.is_alive():
        worker.terminate()
        worker.join(2)
        records.append({'worker_timeout': True})
    if worker.is_alive() or worker.exitcode is None:
        raise RuntimeError('owned_worker_termination_unconfirmed')
    while True:
        try:
            if not connection.poll():
                break
            records.append(connection.recv())
        except (EOFError, OSError):
            break
    connection.close()
    return records


def run_case(ctx, stage, flags):
    read, write = ctx.Pipe()
    counter, stop = ctx.Value('L', 0, lock=False), ctx.Value('b', False, lock=False)
    child = ctx.Process(target=target, args=(write, counter, stop))
    anchor = ctx.Process(target=bootstrap, args=(stop,))
    rescue_anchor = ctx.Process(target=bootstrap, args=(stop,))
    workers = []
    try:
        anchor.start()
        child.start()
        write.close()
        if not read.poll(5):
            raise TimeoutError('own_child_start_timeout')
        pid, address, tid = read.recv()
        if pid != child.pid or not child.is_alive():
            raise RuntimeError('own_child_identity_mismatch')
        initial = synthetic_process_state(pid)
        incoming, outgoing = ctx.Pipe()
        owner = ctx.Process(target=debugger,
                            args=(outgoing, pid, address, 'watchdog', anchor.pid, stage, 'multi'))
        workers.append(owner)
        owner.start()
        outgoing.close()
        owner_records = collect(owner, incoming)
        after_crash = synthetic_process_state(pid) if child.is_alive() else {}
        # No recovery experiment unless the intended fault checkpoint was reached.
        reached = (any(r.get('stage_reached') == stage for r in owner_records)
                   and any(r.get('awaiting_external_termination') for r in owner_records)
                   and owner.exitcode not in (None, 0))
        detached = bool(after_crash) and all(s['debugger_present'] is False for s in after_crash.values())
        if not reached or not detached or not child.is_alive():
            return {'stage': stage, 'attach_flags': flags, 'recovered': False,
                    'owner_records': owner_records, 'reason': 'fault_not_reproduced'}
        # The first anchor may carry the same orphaned suspend count. Prepare
        # the rescue engine against a fresh owned process, not that residue.
        rescue_anchor.start()
        incoming, outgoing = ctx.Pipe()
        helper = ctx.Process(target=rescue, args=(outgoing, pid, rescue_anchor.pid, flags))
        workers.append(helper)
        helper.start()
        outgoing.close()
        rescue_records = collect(helper, incoming)
        before = counter.value
        deadline = time.monotonic() + 2
        while child.is_alive() and counter.value == before and time.monotonic() < deadline:
            time.sleep(.05)
        resumed = child.is_alive() and counter.value > before
        stable = resumed
        if resumed:
            deadline = time.monotonic() + 2
            previous = counter.value
            while time.monotonic() < deadline:
                time.sleep(.1)
                if not child.is_alive() or counter.value <= previous:
                    stable = False
                    break
                previous = counter.value
        final = synthetic_process_state(pid) if child.is_alive() else {}
        return {'stage': stage, 'attach_flags': flags,
                'recovered': bool(resumed and stable and initial == final
                                  and any(r.get('rescue_detached') for r in rescue_records)
                                  and not any('error' in key for r in rescue_records for key in r)),
                'resumed': resumed, 'stable_two_seconds': stable, 'target_alive': child.is_alive(),
                'target_exitcode': child.exitcode, 'owner_records': owner_records,
                'rescue_records': rescue_records, 'initial_threads': initial,
                'after_crash_threads': after_crash, 'final_threads': final,
                'primary_tid': tid}
    finally:
        cleanup_errors = []
        for worker in workers:
            if worker.pid is None:
                continue
            try:
                if worker.is_alive():
                    worker.terminate()
                worker.join(2)
            except Exception:
                cleanup_errors.append('worker_cleanup_failed')
        stop.value = True
        for process in (child, anchor, rescue_anchor):
            if process.pid is None:
                continue
            try:
                process.join(1)
                if process.is_alive():
                    process.terminate()
                    process.join(2)
            except Exception:
                cleanup_errors.append('child_cleanup_failed')
        read.close()
        if cleanup_errors or any(p.pid is not None and (p.is_alive() or p.exitcode is None)
               for p in [*workers, child, anchor, rescue_anchor]):
            raise RuntimeError('own_child_cleanup_unconfirmed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('attached', 'breakpoint', 'cleared'), default='attached')
    parser.add_argument('--attach', choices=('default', 'no-initial-break'), default='default')
    args = parser.parse_args()
    flags = {'default': 0, 'no-initial-break': 8}[args.attach]
    ctx = mp.get_context('spawn')
    fingerprints = helper_fingerprints()
    fingerprints[Path(__file__).name] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    destination = ROOT / f'.local/live-acceptance/recovery-probe-{args.stage}-{args.attach}.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({'research_only': True, 'live_eligible': False,
                                      'state': 'running', 'fingerprints': fingerprints,
                                      'result': {'recovered': False}}), encoding='utf-8')
    try:
        result = run_case(ctx, args.stage, flags)
    except Exception as exc:
        result = {'stage': args.stage, 'attach_flags': flags, 'recovered': False,
                  'error': str(exc) if isinstance(exc, (RuntimeError, TimeoutError)) else type(exc).__name__}
    final_fingerprints = helper_fingerprints()
    final_fingerprints[Path(__file__).name] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if fingerprints != final_fingerprints:
        result['recovered'] = False
        result['error'] = 'experiment_source_changed'
    destination.write_text(json.dumps({'research_only': True, 'live_eligible': False,
                                      'state': 'finished',
                                      'os': platform.version(),
                                      'source_unchanged': fingerprints == final_fingerprints,
                                      'fingerprints': fingerprints, 'result': result}, indent=2),
                           encoding='utf-8')
    print(json.dumps({k: result[k] for k in result if not k.endswith('_threads')}, indent=2))
    return 0 if result['recovered'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
