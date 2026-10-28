"""Owned-target-only analysis-worker failure isolation, NOT debugger recovery.

The actual DbgEng owner remains alive and detaches normally. Owner death is
still covered by the existing failing matrix; this cannot grant live access.
"""
import argparse
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))
from scripts import test_debug_engine as native

ORIGINAL_DEBUGGER = native.debugger
MODES = ('normal', 'crash', 'watchdog')
DBGENG_SHA = 'bfa188b10eb64a94f1eb59bfb0f8d85eb5dfc803cd0f6b5c554816fe311a236e'


def analysis_child(connection, nonce, mode):
    connection.send({'ready': nonce})
    if mode == 'crash':
        os._exit(17)
    if mode == 'watchdog':
        until = time.monotonic() + 5
        while time.monotonic() < until and mp.parent_process().is_alive():
            time.sleep(.05)
        os._exit(43)
    connection.send({'complete': nonce})
    if not connection.poll(2) or connection.recv() != {'ack': nonce}:
        os._exit(44)
    connection.close()


def accept_message(result, message, nonce):
    if message == {'ready': nonce} and not result['ready']:
        result['ready'] = True
        return None
    if message == {'complete': nonce} and result['ready'] and not result['complete']:
        result['complete'] = True
        return {'ack': nonce}
    raise RuntimeError('analysis_protocol_invalid')


def isolate_analysis(mode):
    if mode not in MODES:
        raise RuntimeError('analysis_mode_invalid')
    context, nonce = mp.get_context('spawn'), uuid.uuid4().hex
    read, write = context.Pipe()
    child = context.Process(target=analysis_child, args=(write, nonce, mode))
    result = dict(mode=mode, ready=False, complete=False, timed_out=False, terminated=False)
    try:
        child.start(); write.close()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                if read.poll(.03):
                    message = read.recv()
                    ack = accept_message(result, message, nonce)
                    if ack is not None:
                        read.send(ack)
            except (EOFError, BrokenPipeError):
                break
            if not child.is_alive():
                break
        child.join(.1)
        if child.is_alive():
            result.update(timed_out=True, terminated=True)
            child.terminate()  # Only this owned analysis child, never DbgEng owner.
            child.join(1)
        if child.is_alive():
            raise RuntimeError('analysis_exit_unconfirmed')
        result.update(exited=True, exitcode=child.exitcode)
        return result
    finally:
        if child.pid and child.is_alive():
            child.terminate(); child.join(1)
        if child.pid and not child.is_alive():
            child.close()
            result['process_handle_closed'] = True
        read.close(); write.close()


def observed_expected_failure(row):
    if (not isinstance(row, dict) or row.get('ready') is not True or row.get('exited') is not True or
            row.get('process_handle_closed') is not True):
        return False
    if row.get('mode') == 'normal':
        return row.get('complete') is True and row.get('exitcode') == 0 and row.get('terminated') is False
    if row.get('mode') == 'crash':
        return row.get('exitcode') == 17 and row.get('complete') is False and row.get('terminated') is False
    return (row.get('mode') == 'watchdog' and row.get('timed_out') is True and
            row.get('terminated') is True and row.get('complete') is False and
            type(row.get('exitcode')) is int and row['exitcode'] != 0)


def owner(connection, pid, address, mode, bootstrap_pid, stage, topology):
    class GuardedConnection:
        def send(self, record):
            connection.send(record)
            if record.get('stage_reached') == 'breakpoint':
                outcome = isolate_analysis(mode)
                connection.send({'analysis_isolation': outcome})
                if not observed_expected_failure(outcome):
                    raise RuntimeError('analysis_expected_outcome_not_observed')
    # This is intentionally a normal-owner test. No owner-death claim is made.
    ORIGINAL_DEBUGGER(GuardedConnection(), pid, address, 'normal', bootstrap_pid, stage, topology)


def case_passed(case):
    rows = [r['analysis_isolation'] for r in case.get('records', []) if 'analysis_isolation' in r]
    return bool(case.get('passed') is True and len(rows) == 1 and observed_expected_failure(rows[0]) and
        any(r.get('detached') is True for r in case['records']) and
        case.get('all_threads_checked') is True and case.get('stable_two_seconds') is True and
        case.get('initial_threads') == case.get('final_threads'))


def fingerprints():
    result = native.helper_fingerprints()
    result['probe_debugger_analysis_guard.py'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--run-synthetic', action='store_true')
    args = parser.parse_args(argv)
    if not args.run_synthetic:
        print(json.dumps({'state': 'disabled', 'live_eligible': False}))
        return 0
    source = fingerprints()
    if source['dbgeng.dll'] != DBGENG_SHA:
        raise RuntimeError('analysis_guard_dbgeng_fingerprint_mismatch')
    cases = []
    previous = native.debugger
    try:
        native.debugger = owner
        for mode in MODES:
            case = native.test_mode(mp.get_context('spawn'), mode)
            case['analysis_guard_passed'] = case_passed(case)
            cases.append(case)
            print(json.dumps({'mode': mode, 'analysis_guard_passed': case['analysis_guard_passed']}), flush=True)
    finally:
        native.debugger = previous
    report = dict(kind='owned_analysis_failure_isolation', source_unchanged=source == fingerprints(),
        fingerprints=source, cases=cases, live_eligible=False, owner_failure_recovered=False)
    report['passed'] = report['source_unchanged'] and len(cases) == 3 and all(c['analysis_guard_passed'] for c in cases)
    destination = ROOT / '.local/live-acceptance' / ('debugger-analysis-guard-' + uuid.uuid4().hex + '.json')
    destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'path': str(destination), 'passed': report['passed'], 'live_eligible': False}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
