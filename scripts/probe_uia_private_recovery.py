"""Own-child-only UIA private ETW cleanup research, disabled by default.

Run --stage precheck first. A matching successful precheck is required before
--stage matrix. Four admitted faults affect only a newly spawned controller;
the other newly spawned target must survive verified session cleanup. The
self-target crash is NOT admitted: target death plus Query4201 cannot prove
shared configuration removal. No windows, UIA tree, real client, arbitrary PID,
debugger, elevation, global trace enumeration or permission changes are used.
"""
import argparse
import ctypes as c
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import probe_uia_private_scope as normal

FROZEN_NORMAL = '37784b48b650d8209eb466fa7a1804942d34a2db2b3fce5ed0315d03c43e5a65'
EXIT_CODE = 79
FAULTS = ('created_before_ack_exit', 'enabled_exit_other',
          'enabled_unresponsive_other', 'command_eof_other')
NOT_ADMITTED = {'case': 'enabled_exit_self', 'executed': False, 'admitted': False,
                'reason': 'target_death_and_query_absence_do_not_prove_shared_configuration_removed'}


def fingerprints():
    result = normal.fingerprints()
    result[Path(__file__).name] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if result['probe_uia_private_scope.py'] != FROZEN_NORMAL:
        raise RuntimeError('normal_probe_fingerprint_changed')
    return result


def write_atomic(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def checkpoint(directory, token, kind, value):
    path = Path(directory) / (token + '-' + kind + '.json')
    if path.exists():
        raise RuntimeError('checkpoint_already_exists')
    write_atomic(path, value)


class FaultPipe:
    """Intercept only native-success messages from the frozen normal worker."""
    def __init__(self, primary, evidence, mode, journal_directory, token):
        self.primary, self.evidence, self.mode = primary, evidence, mode
        self.directory, self.token = journal_directory, token

    def poll(self, timeout):
        return self.primary.poll(timeout)

    def recv(self):
        return self.primary.recv()

    def close(self):
        self.primary.close()

    def send(self, value):
        kind = ('created' if 'created' in value else 'enabled' if 'phase_ready' in value
                else 'finished' if 'finished' in value else None)
        if kind:
            message = {'checkpoint': kind, 'token': self.token, 'value': value,
                       'normal_ack': False, 'owner_pid': os.getpid()}
            checkpoint(self.directory, self.token, kind, message)
            self.evidence.send(message)
            if ((kind == 'created' and self.mode == 'created_before_ack_exit') or
                    (kind == 'enabled' and self.mode == 'enabled_exit_other')):
                os._exit(EXIT_CODE)  # Only this disposable, non-debugging owner.
            if kind == 'enabled' and self.mode == 'enabled_unresponsive_other':
                time.sleep(8)  # Bounded injected stall; parent must time out and terminate it.
        self.primary.send(value)


def owner_child(primary, evidence, directory, token, mode, journals):
    try:
        fingerprints()
        wrapped = FaultPipe(primary, evidence, mode, journals, token)
        normal.source_child(wrapped, directory, token, True)
    finally:
        evidence.close()


class TargetLease:
    """Query/synchronize handle pins the target identity through cleanup."""
    def __init__(self, identity, process):
        self.identity, self.process = identity, process
        self.kernel = c.WinDLL('kernel32', use_last_error=True)
        self.open = normal.uia.call(self.kernel, 'OpenProcess', normal.uia.PTR,
                                   [normal.uia.U32, c.c_int, normal.uia.U32])
        self.wait = normal.uia.call(self.kernel, 'WaitForSingleObject', normal.uia.U32,
                                   [normal.uia.PTR, normal.uia.U32])
        self.close_handle = normal.uia.call(self.kernel, 'CloseHandle', c.c_int, [normal.uia.PTR])
        self.handle = None
        try:
            if not process.is_alive() or process.pid != identity['pid']:
                raise RuntimeError('own_target_not_alive')
            self.handle = self.open(0x101000, False, identity['pid'])
            if not self.handle or normal.creation_time(self.kernel, self.handle) != identity['created']:
                raise RuntimeError('own_target_identity_changed')
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        if (not self.handle or not self.process.is_alive() or self.wait(self.handle, 0) != 258 or
                normal.creation_time(self.kernel, self.handle) != self.identity['created']):
            raise RuntimeError('own_target_lost')

    def close(self):
        if self.handle:
            result = bool(self.close_handle(self.handle))
            self.handle = None
            return result
        return True


class CheckpointMismatch(RuntimeError):
    def __init__(self, value):
        super().__init__('fault_checkpoint_mismatch')
        self.value = value


def receive_checkpoint(pipe, token, kind, timeout=4):
    value = normal.private.receive(pipe, timeout)
    if value.get('token') != token or value.get('checkpoint') != kind or value.get('normal_ack') is not False:
        raise CheckpointMismatch(value)
    return value


def verify_precheck(report, expected):
    return bool(report.get('stage') == 'precheck' and report.get('passed') is True and
                report.get('live_diagnostic_ready') is False and
                report.get('fingerprints') == expected and report.get('source_unchanged') is True and
                report.get('os') == platform.version() and report.get('dll_sha256') == normal.uia.DLL_HASH and
                len(report.get('cases', [])) == 1 and
                report['cases'][0].get('case') == 'external_controller_precheck' and
                case_passed(report['cases'][0]))


def stop_proven(result):
    return bool(result.get('actual_name_and_file_verified') is True and result.get('stop_status') == 0 and
                result.get('query_after') == 4201 and result.get('events_lost') == 0 and result.get('absent') is True)


def case_cleanup_proven(result):
    if result.get('case') == 'command_eof_other':
        owner_clean = result.get('owner_finally', {}).get('cleanup', [])
        return bool(len(owner_clean) == 1 and stop_proven(owner_clean[0]) and
                    result.get('external_cleanup') == {'query_before': 4201, 'absent': True})
    return stop_proven(result.get('external_cleanup', {}))


def final_cleanup_proven(children):
    """Only this run's owner/fallback Stop evidence can close an uncertain session.

    The frozen normal cleanup treats Query4201 as absence. For an injected owner
    fault that does not prove shared configuration removal; keep its evidence
    unless an actual, identity-checked Stop was recorded while cleaning up.
    """
    if not children or children[0].get('index') != 0:
        return False
    owner = children[0]
    if stop_proven(owner.get('fallback_cleanup', {})):
        return True
    cleanup = owner.get('result', {}).get('cleanup', [])
    return bool(len(cleanup) == 1 and stop_proven(cleanup[0]))


def case_passed(result):
    if result.get('error') or result.get('cleanup_unknown') or not result.get('target_alive_after_cleanup'):
        return False
    if result.get('post_cleanup_results') != [normal.INVALID] * normal.COUNT:
        return False
    children = result.get('children', [])
    if len(children) != 2 or children[1].get('index') != 1:
        return False
    target = children[1]
    if (target.get('terminated') is not False or target.get('exitcode') != 0 or
            target.get('result', {}).get('error') or
            target.get('result', {}).get('dll_unchanged') is not True or
            result.get('dll_unchanged') is not True):
        return False
    clean = case_cleanup_proven(result)
    fault = result.get('case')
    reached = (result.get('created_observed') and (fault == 'created_before_ack_exit' or
               result.get('enabled_observed')))
    if fault in ('created_before_ack_exit', 'enabled_exit_other'):
        reached = reached and result.get('owner_fault_exitcode') == EXIT_CODE
    elif fault == 'enabled_unresponsive_other':
        reached = reached and result.get('owner_timeout_observed') and result.get('owner_terminated_before_cleanup')
    elif fault == 'command_eof_other':
        reached = reached and result.get('owner_fault_exitcode') == 0
    elif fault == 'external_controller_precheck':
        reached = reached and result.get('owner_alive_during_external_stop')
    else:
        return False
    if fault in ('external_controller_precheck', 'command_eof_other'):
        owner = children[0]
        finished = (result.get('owner_finally', {}) if fault == 'command_eof_other'
                    else owner.get('result', {}))
        expected_error = 'EOFError:' if fault == 'command_eof_other' else None
        if (owner.get('terminated') is not False or owner.get('exitcode') != 0 or
                finished.get('dll_unchanged') is not True or
                finished.get('process_handles_closed') != [True, True] or
                finished.get('error') != expected_error):
            return False
    return bool(clean and reached and result.get('all_children_dead') and
                result.get('target_lease_closed') is True and result.get('temporary_files_removed') is True and
                result.get('source_unchanged') is True)


def run_case(mode, journals, source):
    if mode not in ('external_controller_precheck',) + FAULTS:
        raise RuntimeError('case_not_admitted')
    token, index = uuid.uuid4().hex, 1  # Every admitted session targets the OTHER child.
    directory = ROOT / '.local/live-acceptance'
    temporary = Path(tempfile.mkdtemp(prefix='uia-recovery-own-', dir=directory))
    intent_path = journals / (token + '-intent.json')
    entries, identities, lease, audit, started_request, cleaned = [], [], None, None, False, False
    result = {'case': mode, 'executed': True, 'admitted': True, 'passed': False,
              'live_diagnostic_ready': False, 'token': token, 'target_index': index}
    intent = {'case': mode, 'token': token, 'session': normal.session_name(token, index),
              'target_index': index, 'etl': str(temporary / 'phase1.etl'),
              'fingerprints': source, 'os': platform.version(), 'dll_sha256': normal.uia.DLL_HASH}
    try:
        write_atomic(intent_path, dict(intent, state='preparing'))
        ctx = mp.get_context('spawn')
        for child_index in range(2):
            primary, remote = ctx.Pipe()
            if child_index == 0:
                audit, audit_remote = ctx.Pipe(duplex=False)
                process = ctx.Process(target=owner_child,
                    args=(remote, audit_remote, str(temporary), token, mode, str(journals)))
            else:
                process = ctx.Process(target=normal.source_child, args=(remote, str(temporary), token, False))
            entries.append((process, primary))
            try:
                process.start()
            finally:
                remote.close()
                if child_index == 0:
                    audit_remote.close()
            reply = normal.private.receive(primary, 5)
            own = reply.get('ready', {})
            if own.get('pid') != process.pid or own.get('dll_sha256') != normal.uia.DLL_HASH:
                raise RuntimeError('own_source_handshake_failed')
            identities.append(own)
        owner, command = entries[0]
        target, target_pipe = entries[1]
        lease = TargetLease(identities[1], target)
        command.send({'bind': identities})
        if normal.private.receive(command, 1) != {'bound': True}:
            raise RuntimeError('own_source_bind_failed')
        intent['identities'] = [{key: identity[key] for key in ('pid', 'tid', 'created')}
                                for identity in identities]
        lease.check()
        write_atomic(intent_path, dict(intent, state='phase_intended'))
        started_request = True
        command.send({'phase': index})
        created = receive_checkpoint(audit, token, 'created')
        if created['value'] != {'created': index, 'name': normal.session_name(token, index)}:
            raise RuntimeError('created_checkpoint_scope_mismatch')
        result['created_observed'] = True
        write_atomic(intent_path, dict(intent, state='created_observed', normal_ack=False))
        if mode != 'created_before_ack_exit':
            if normal.private.receive(command, 1) != created['value']:
                raise RuntimeError('normal_created_ack_mismatch')
            result['normal_created_ack_received'] = True
            enabled = receive_checkpoint(audit, token, 'enabled')
            if enabled['value'] != {'phase_ready': index}:
                raise RuntimeError('enabled_checkpoint_scope_mismatch')
            result['enabled_observed'] = True
        if mode in ('created_before_ack_exit', 'enabled_exit_other'):
            owner.join(2)
            if owner.is_alive() or owner.exitcode != EXIT_CODE:
                raise RuntimeError('injected_owner_exit_not_observed')
            result['owner_fault_exitcode'] = owner.exitcode
        elif mode == 'enabled_unresponsive_other':
            owner.join(.5)
            if not owner.is_alive():
                raise RuntimeError('injected_timeout_not_observed')
            result['owner_timeout_observed'] = True
            owner.terminate()
            owner.join(2)
            result['owner_terminated_before_cleanup'] = not owner.is_alive()
            if owner.is_alive():
                raise RuntimeError('owner_termination_unconfirmed')
        else:
            if normal.private.receive(command, 1) != {'phase_ready': index}:
                raise RuntimeError('normal_phase_ready_mismatch')
            if mode == 'command_eof_other':
                command.close()
                finish = receive_checkpoint(audit, token, 'finished')
                result['owner_finally'] = finish['value']['finished']
                owner.join(2)
                result['owner_fault_exitcode'] = owner.exitcode
                if owner.is_alive() or owner.exitcode != 0:
                    raise RuntimeError('eof_owner_exit_unconfirmed')
            else:
                target_pipe.send({'emit': 1})
                if normal.private.receive(target_pipe, 1) != {'emitted': 1, 'results': [normal.INVALID] * normal.COUNT}:
                    raise RuntimeError('precheck_source_failed')
                result['owner_alive_during_external_stop'] = owner.is_alive()
        lease.check()
        owned_scope = normal.new_scope(token, index, identities, temporary)
        result['external_cleanup'] = normal.stop_owned_scope(normal.private.controller(), owned_scope)
        cleaned = case_cleanup_proven(result)
        if not cleaned:
            raise RuntimeError('external_cleanup_unproven')
        lease.check()
        result['target_alive_after_cleanup'] = True
        target_pipe.send({'emit': 0})
        continuation = normal.private.receive(target_pipe, 1)
        if continuation != {'emitted': 0, 'results': [normal.INVALID] * normal.COUNT}:
            raise RuntimeError('surviving_target_source_failed')
        result['post_cleanup_results'] = continuation['results']
        lease.check()
        write_atomic(intent_path, dict(intent, state='cleanup_observed', external_cleanup=result['external_cleanup']))
    except BaseException as exc:
        if isinstance(exc, CheckpointMismatch) and exc.value.get('token') == token:
            failure = exc.value.get('value', {}).get('finished', {})
            result['source_failure'] = failure
            if failure.get('incomplete_phase', {}).get('start_status') not in (None, 0):
                started_request = False  # An explicit failed Start owns no session.
        result['error'] = type(exc).__name__ + ':' + str(exc)
    finally:
        # Frozen cleanup stops/terminates owner before an identity-proven scoped fallback,
        # and only then asks the still separate target child to exit.
        result['children'], normal_unknown = normal.finish_children(
            entries, index if started_request and not cleaned else None, False, token, identities, temporary)
        result['session_cleanup_proven'] = cleaned or final_cleanup_proven(result['children'])
        result['cleanup_unknown'] = bool(normal_unknown or
            (started_request and not result['session_cleanup_proven']))
        result['all_children_dead'] = all(not process.is_alive() for process, _ in entries)
        result['target_lease_closed'] = normal.private.safe(lease.close) if lease else True
        if audit is not None:
            result['evidence_pipe_close'] = normal.private.safe(audit.close)
        if result['all_children_dead'] and not result['cleanup_unknown']:
            def remove_temp():
                resolved = temporary.resolve()
                if resolved.parent != directory.resolve() or not resolved.name.startswith('uia-recovery-own-'):
                    raise RuntimeError('temporary_scope_changed')
                shutil.rmtree(resolved)
                return not resolved.exists()
            result['temporary_files_removed'] = normal.private.safe(remove_temp)
        elif result['cleanup_unknown']:
            result['temporary_files_retained'] = temporary.exists()
        result['source_unchanged'] = normal.private.safe(lambda: fingerprints() == source)
        result['dll_unchanged'] = normal.private.safe(lambda: normal.uia.uia_hash() == normal.uia.DLL_HASH)
        result['passed'] = case_passed(result)
        write_atomic(intent_path, dict(intent, state='finished', result=result))
    return result


def report_path(stage, source):
    digest = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()[:12]
    return ROOT / '.local/live-acceptance' / ('uia-private-recovery-' + stage + '-' + digest + '.json')


def require_precheck(source):
    prior = json.loads(report_path('precheck', source).read_text(encoding='utf-8'))
    if not verify_precheck(prior, source):
        raise RuntimeError('matching_external_controller_precheck_required')


def run(stage, destination, source):
    report = {'stage': stage, 'passed': False, 'live_diagnostic_ready': False,
              'dbgeng_recovery_verified': False, 'windows_operated': False,
              'fingerprints': source, 'os': platform.version(), 'dll_sha256': normal.uia.DLL_HASH, 'cases': []}
    if stage == 'matrix':
        require_precheck(source)
        report['not_admitted'] = [NOT_ADMITTED]
    journals = destination.parent / 'uia-private-recovery-journals'
    journals.mkdir(exist_ok=True)
    for mode in (('external_controller_precheck',) if stage == 'precheck' else FAULTS):
        result = run_case(mode, journals, source)
        report['cases'].append(result)
        write_atomic(destination, dict(report, state='running'))
        if not result['passed']:
            break  # No retry, no subsequent fault after a failed recovery.
    report['source_unchanged'] = normal.private.safe(lambda: fingerprints() == source)
    expected = 1 if stage == 'precheck' else len(FAULTS)
    report['admitted_cases_passed'] = bool(len(report['cases']) == expected and
                                           all(value['passed'] for value in report['cases']))
    report['passed'] = bool(stage == 'precheck' and report['admitted_cases_passed'] and report['source_unchanged'] is True)
    if stage == 'matrix':
        report['remote_owner_recovery_passed'] = report['admitted_cases_passed'] and report['source_unchanged'] is True
        report['all_five_cases_verified'] = False
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-synthetic', action='store_true')
    parser.add_argument('--stage', choices=('precheck', 'matrix'), default='precheck')
    args = parser.parse_args()
    if not args.run_synthetic:
        print(json.dumps({'trace_started': False, 'live_diagnostic_ready': False,
                          'stage': args.stage, 'self_target_admitted': False}))
        return 0
    source = fingerprints()
    if args.stage == 'matrix':
        require_precheck(source)  # A missing prerequisite does not consume an attempt.
    destination = report_path(args.stage, source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # One attempt for each exact source revision and stage; retain failures intact.
    with destination.open('x', encoding='utf-8') as stream:
        json.dump({'state': 'running', 'passed': False, 'fingerprints': source}, stream)
    try:
        report = run(args.stage, destination, source)
    except BaseException as exc:
        report = {'stage': args.stage, 'passed': False, 'live_diagnostic_ready': False,
                  'fingerprints': source, 'error': type(exc).__name__ + ':' + str(exc)}
    report['state'] = 'finished'
    write_atomic(destination, report)
    print(json.dumps({'report': str(destination), 'passed': report['passed'],
                      'remote_owner_recovery_passed': report.get('remote_owner_recovery_passed', False),
                      'live_diagnostic_ready': False}, indent=2))
    return 0 if report.get('remote_owner_recovery_passed', report['passed']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
