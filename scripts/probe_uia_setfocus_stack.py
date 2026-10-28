"""Disabled-by-default SetFocus stack calibration on one owned UIA client.

Only the frozen modern_default_tree positive control runs. Three direct children
are owned by this controller: private-desktop GUI, UIA MTA client and private ETW
owner. No external PID, WeChat, TextChanged, foreground activation or live budget
is accepted. ETL and intent evidence are retained, especially on uncertain Stop.
"""
import argparse
import ctypes as c
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import struct
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import probe_uia_autofocus_cross_process as cross
from scripts import probe_uia_private_scope as normal
from scripts import probe_uia_private_recovery as recovery
from scripts import uia_setfocus_evidence as evidence
from scripts.inspect_focus_events import Metadata, event_descriptors, parse_event

base, tree, private = cross.base, cross.tree, normal.private
PHASE = 'modern_default_tree'
ORIGINAL_PHASES = tuple(tree.PHASES)
# Owned four-phase timing measured 2.094s to complete-request plus the frozen
# 0.25s readback acknowledgement. A two-second window cannot cover that source.
# Retain three additional seconds before the unchanged client's hard watchdog.
CAPTURE_SECONDS, START_MARGIN = 3.0, 6.0
FROZEN_CROSS = '793d70854906bf80cf0f2fc0c4ee9144551ab1d950205070dd2120b5275f4675'
CAPTURE_BOUNDARY = 'second_check_ack_before_setvalue_v1'
_OWNED_RECOVERY = []


def fingerprints():
    result = cross.fingerprints()
    recovery.fingerprints()  # Includes the reviewed normal-controller pin.
    if result['scripts/probe_uia_autofocus_cross_process.py'] != FROZEN_CROSS:
        raise RuntimeError('frozen_cross_process_canary_changed')
    for module in (normal, recovery, private, evidence, sys.modules[__name__]):
        path = Path(module.__file__)
        result[str(path.relative_to(ROOT)).replace('\\', '/')] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def dll_path():
    return Path(os.environ['SystemRoot']) / 'System32/UIAutomationCore.dll'


def exact_dll():
    if hashlib.sha256(dll_path().read_bytes()).hexdigest() != evidence.DLL_SHA256:
        raise RuntimeError('unreviewed_uia_dll')


def manifest():
    """Read all metadata before starting any disposable child's lifetime."""
    metadata = Metadata()
    provider = private.GUID.from_buffer_copy(evidence.PROVIDER)
    descriptors = event_descriptors(metadata.buffer('TdhEnumerateManifestProviderEvents', provider))
    if not 2 <= len(descriptors) <= 512:
        raise RuntimeError('manifest_count_invalid')
    events = []
    for raw in descriptors:
        blob = metadata.buffer('TdhGetManifestEventInformation', provider,
                               (c.c_ubyte * 16).from_buffer_copy(raw))
        if blob[:16] != evidence.PROVIDER or blob[32:48] != raw:
            raise RuntimeError('manifest_identity_changed')
        events.append(parse_event(blob))
    exact_dll()
    return evidence.schema_table(events)


def merge_modules(initial, final):
    """Never infer a transient mapping from two incompatible snapshots."""
    schemas = {evidence.ENTRY: {'id': 335, 'payload_empty': True},
               evidence.EXIT: {'id': 336, 'payload_empty': True}}
    first = evidence.SetFocusEvidence(1, 1, schemas, initial).modules
    last = evidence.SetFocusEvidence(1, 1, schemas, final).modules
    # Every initial module must survive at the same address/name/size. New loads
    # may be merged only when the resulting ranges remain unambiguous.
    if not set(first).issubset(set(last)):
        raise ValueError('module_unloaded_or_replaced')
    return evidence.SetFocusEvidence(1, 1, schemas, sorted(set(first) | set(last))).modules


class StopBarrier:
    """Pure admission state: a target death before proven Stop is irreversible."""
    def __init__(self, identity, nonce):
        self.identity, self.nonce = dict(identity), nonce
        self.requested = self.enabled = self.stopped = self.target_lost = False
        self.result = None

    def start(self, now, client_deadline):
        if self.requested or client_deadline - now < START_MARGIN:
            raise RuntimeError('capture_start_deadline_or_reuse')
        self.requested = True  # Persist intent before the owner may StartTrace.

    def observe(self, alive):
        if self.requested and not self.stopped and not alive:
            self.target_lost = True

    def accept(self, message, alive):
        self.observe(alive)
        if (not self.requested or not isinstance(message, dict) or message.get('event') != 'stopped' or
                message.get('nonce') != self.nonce or
                message.get('identity') != self.identity or
                message.get('alive_before_stop') is not True or message.get('alive_after_stop') is not True or
                not isinstance(message.get('stop'), dict) or
                any(type(message['stop'].get(k)) is not int for k in ('stop_status', 'query_after', 'events_lost')) or
                not recovery.stop_proven(message['stop']) or self.target_lost or not alive):
            return False
        self.stopped, self.result = True, message
        return True

    @property
    def may_exit(self):
        return not self.requested or (self.stopped and not self.target_lost)


def self_snapshot():
    exact_dll()
    kernel = c.WinDLL('kernel32')
    return {'identity': cross.own_identity(),
            'tid': base.call(kernel, 'GetCurrentThreadId', base.U32, [])(),
            'dll_sha256': evidence.DLL_SHA256, 'modules': normal.uia.module_ranges()}


def owned_ready(message, process_identity):
    """Bind the ready reply to the original multiprocessing process handle."""
    return bool(isinstance(message, dict) and message.get('ready') == process_identity and
                message.get('mta_verified') is True and
                all(type(process_identity.get(k)) is int and process_identity[k] > 0 for k in ('pid', 'created')))


def boundary_evidence(target, *, complete=False):
    """The frozen modern branch has exactly two pre-write GUI checks."""
    return {'version': CAPTURE_BOUNDARY, 'phase': PHASE,
            'acknowledged': ['prepare', 'check', 'check'] + (['complete'] if complete else []),
            'checks_before_start': 2, 'prewrite_focus': target['b'], 'prewrite_focus_events': 0}


class CaptureWindow:
    """Pure protocol gate; discovery never starts the bounded capture."""
    ORDER = ('prepare', 'check', 'check', 'complete')

    def __init__(self, phase, target):
        if phase != PHASE or not isinstance(target, dict) or not cross.target_valid(target, target.get('pid')):
            raise RuntimeError('capture_phase_or_target_mismatch')
        self.target, self.acknowledged, self.pending, self.failed = dict(target), [], None, False

    def reject(self, reason):
        self.failed = True
        raise RuntimeError(reason)

    def before(self, action, phase):
        # Reject extra/missing exchanges before forwarding any GUI operation.
        if (self.failed or phase != PHASE or self.pending is not None or
                len(self.acknowledged) >= len(self.ORDER) or action != self.ORDER[len(self.acknowledged)]):
            self.reject('capture_exchange_order')
        self.pending = action

    def acknowledge(self, value):
        if self.failed or self.pending is None or not isinstance(value, dict) or 'error' in value:
            self.reject('capture_ack_invalid')
        action = self.pending
        if action != 'complete':
            # The frozen GUI's prepare reply contains only focus; each check
            # contains focus + focus_events. No extra COM lookup is introduced.
            if (type(value.get('focus')) is not int or value['focus'] != self.target['b'] or
                    value.get('event_overflow', False) is not False or
                    value.get('focus_events', [] if action == 'prepare' else None) != []):
                self.reject('capture_prewrite_focus_changed')
        self.acknowledged.append(action)
        self.pending = None
        if len(self.acknowledged) == 3:
            return 'start'
        return 'stop' if action == 'complete' else None

    def evidence(self):
        if self.failed or self.pending is not None or len(self.acknowledged) not in (3, 4):
            self.reject('capture_boundary_incomplete')
        return boundary_evidence(self.target, complete=len(self.acknowledged) == 4)

    def finish(self):
        if self.failed or self.pending is not None or tuple(self.acknowledged) != self.ORDER:
            self.reject('capture_phase_incomplete')
        return self.evidence()


def client_main(relay, control, trace, desktop_id, nonce):
    original = tree.tree_phase
    def phase(create, bindings, target, exchange, name, records=None):
        window = CaptureWindow(name, target)
        # Keep the exact DLL mapped throughout both snapshots and all COM calls.
        loaded = c.WinDLL(str(dll_path()), winmode=0x800)
        active = False
        def checkpoint(action, which):
            nonlocal active
            window.before(action, which)
            value = exchange(action, which)
            decision = window.acknowledge(value)
            if decision == 'start':
                active = True  # An uncertain request must not let this client exit.
                trace.send({'event': 'start', 'nonce': nonce, 'phase': name, 'snapshot': self_snapshot(),
                            'boundary': window.evidence()})
                wait_for('enabled')
            elif decision == 'stop':
                trace.send({'event': 'stop', 'nonce': nonce, 'phase': name, 'snapshot': self_snapshot(),
                            'boundary': window.evidence()})
                wait_for('stopped')
                active = False
            return value
        def wait_for(event):
            # The frozen 15-second watchdog remains authoritative. On EOF or an
            # invalid acknowledgement stay alive until root proves Stop or that
            # watchdog ends us; never turn a missing Stop into graceful success.
            while True:
                try:
                    if trace.poll(.02):
                        message = trace.recv()
                        if message == {'event': event, 'nonce': nonce, 'phase': PHASE}:
                            return
                except (OSError, EOFError):
                    time.sleep(.02)
        try:
            # cross loops one phase, but the frozen implementation's positional
            # branch tests must see its original four-phase tuple.
            with tree.replace_attributes(tree, PHASES=ORIGINAL_PHASES):
                report = original(create, bindings, target, checkpoint, name, records)
            report['capture_boundary'] = window.finish()
            return report
        finally:
            if active:
                try:
                    trace.send({'event': 'abort', 'nonce': nonce, 'phase': name})
                except (OSError, EOFError):
                    pass
                wait_for('stopped')
            del loaded
    try:
        with tree.replace_attributes(tree, PHASES=(PHASE,), tree_phase=phase):
            cross.client_main(relay, control, desktop_id, nonce)
    finally:
        trace.close()


def provider_main(pipe, desktop_id, relay, identity, nonce):
    with tree.replace_attributes(tree, PHASES=(PHASE,)):
        cross.provider_main(pipe, desktop_id, relay, identity, nonce)


def owner_main(pipe, nonce, name, path, journal):
    """Independent bounded owner; root can recover using the same scope."""
    held = scope = trace = None
    started = stopped = False
    timing = {}
    identity, final = {}, {'target_handle_closed': False}
    api = private.controller()
    def stop(reason):
        nonlocal stopped
        timing['stop_enter'] = time.monotonic()
        before = held is not None and held.wait(0) == 258
        result = normal.stop_owned_scope(api, scope, trace) if scope else {}
        timing['stop_return'] = time.monotonic()
        after = held is not None and held.wait(0) == 258
        message = {'event': 'stopped', 'nonce': nonce, 'identity': identity, 'reason': reason,
                   'alive_before_stop': before, 'alive_after_stop': after, 'stop': result,
                   'timing': dict(timing)}
        recovery.write_atomic(Path(journal), message)
        stopped = True
        pipe.send(message)
    try:
        private.check_abi()
        pipe.send({'event': 'owner_ready', 'nonce': nonce, 'identity': cross.own_identity()})
        command = cross.receive(pipe, time.monotonic() + 10)
        if command.get('event') != 'start' or command.get('nonce') != nonce:
            raise RuntimeError('owner_start_protocol')
        identity = command['identity']
        if command['client_deadline'] - time.monotonic() < START_MARGIN:
            raise RuntimeError('owner_start_margin')
        held = cross.HeldProcess(identity)
        scope = normal.Scope(name, evidence.PROVIDER, identity['pid'], str(path))
        trace = private.U64()
        recovery.write_atomic(Path(journal), {'event': 'start_pending', 'nonce': nonce,
            'identity': identity, 'name': name, 'path': str(path)})
        timing['capture_begin'] = time.monotonic()
        capture_deadline = timing['capture_begin'] + CAPTURE_SECONDS
        started = True  # A missing Start acknowledgement requires recovery.
        storage, _ = scope.properties()
        status = api.StartTraceW(c.byref(trace), name, storage)
        timing['start_return'] = time.monotonic()
        if status:
            raise RuntimeError('start_trace_failed_' + str(status))
        if held.wait(0) != 258 or time.monotonic() >= capture_deadline:
            raise RuntimeError('owner_start_target_or_deadline')
        params = scope.enable()
        timing['enable_enter'] = time.monotonic()
        status = api.EnableTraceEx2(trace, private.GUID.from_buffer_copy(evidence.PROVIDER),
            1, 4, 0x10000, 0, 500, c.byref(params))
        timing['enable_return'] = time.monotonic()
        if status or held.wait(0) != 258 or time.monotonic() >= capture_deadline:
            raise RuntimeError('enable_trace_or_deadline_failed')
        timing['enabled_sent'] = time.monotonic()
        pipe.send({'event': 'enabled', 'nonce': nonce, 'identity': identity})
        while time.monotonic() < capture_deadline and mp.parent_process().is_alive():
            if held.wait(0) != 258:
                stop('target_died_before_stop')
                break
            if pipe.poll(.01):
                request = pipe.recv()
                if request != {'event': 'stop', 'nonce': nonce}:
                    raise RuntimeError('owner_stop_protocol')
                stop('requested')
                break
        if not stopped:
            stop('capture_deadline')
        # Remain an independently owned process while the target is released.
        if cross.receive(pipe, time.monotonic() + 12) != {'event': 'close', 'nonce': nonce}:
            raise RuntimeError('owner_close_protocol')
    except BaseException as exc:
        final['error'] = str(exc) if type(exc) is RuntimeError else type(exc).__name__
    finally:
        if started and not stopped:
            try:
                stop('owner_finally')
            except BaseException:
                final['stop_error'] = True
        final['target_handle_closed'] = held.close() if held else False
        try:
            pipe.send({'event': 'owner_finished', 'nonce': nonce, 'result': final})
        except (OSError, EOFError):
            pass
        pipe.close()


def read_stack(record):
    """Called lazily only for admitted target 335/336 headers; never payload."""
    if not 1 <= record.extended_count <= 16 or not record.extended:
        raise ValueError('stack_extensions_invalid')
    stacks = [record.extended[i] for i in range(record.extended_count) if record.extended[i].kind == 6]
    if len(stacks) != 1:
        raise ValueError('stack_extension_ambiguous')
    item = stacks[0]
    if (item.reserved or item.linkage or not item.data or not 16 <= item.size <= 520 or item.size % 8):
        raise ValueError('stack_extension_invalid')
    return list(struct.unpack('<' + 'Q' * (item.size // 8 - 1), c.string_at(item.data + 8, item.size - 8)))


class NativeEvidence:
    def __init__(self, inner):
        self.inner, self.metadata, self.callback_error = inner, 0, False
        self.selected = self.opposite = (inner.pid, inner.tid)
        self.counts = {}  # Required only by the frozen bounded file consumer.

    def consume(self, pointer):
        try:
            record = pointer.contents
            header = record.header
            if bytes(header.provider) == private.SESSION_METADATA:
                self.metadata += 1
                if self.metadata > 32:
                    self.callback_error = True
                return
            value = {'provider': bytes(header.provider), 'pid': header.pid, 'tid': header.tid,
                'descriptor': bytes(header.descriptor), 'flags': header.flags,
                'data_length': record.data_length, 'time': header.time}
            self.inner.consume(value, lambda: read_stack(record))
        except BaseException:
            self.callback_error = True


def phase_qualified(result, target, identity):
    if not isinstance(result, dict):
        return False
    phases = result.get('phases', [])
    if (result.get('error') or result.get('mta_verified') is not True or result.get('target') != target or
            not isinstance(phases, list) or len(phases) != 1 or not isinstance(phases[0], dict) or
            phases[0].get('phase') != PHASE):
        return False
    p = phases[0]
    finish = p.get('remote_finish', {})
    return bool(not p.get('error') and not p.get('event_overflow') and
        p.get('remote_client_pid') == identity.get('pid') and p.get('remote_client_created') == identity.get('created') and
        p.get('remote_mta_verified') is True and p.get('focus_before') == target.get('b') and
        p.get('focus') == target.get('a') and [target.get('a'), 'set'] in p.get('focus_events', []) and
        p.get('pattern_path') == 'root_findall_edit_id' and p.get('synthetic_input_id') == '101' and
        p.get('configured_auto_set_focus') in (1, -1) and p.get('real_candidate_used') is False and
        all(p.get(key) is True for key in ('fresh_client', 'attempted', 'uia_full_readback_equal',
            'native_full_readback_equal', 'identity_stable', 'remote_exit_before_provider_cleanup',
            'relay_client_handle_closed')) and not finish.get('error') and
        all(finish.get('cleanup', {}).get(k) is True for k in ('com_uninitialized', 'provider_handle_closed',
            'desktop_restored', 'desktop_closed')))


def _complete_pass(report):
    """All native evidence is bound to owned identities, complete window and Stop."""
    if not isinstance(report, dict):
        return False
    remote, provider, owner = (report.get(k, {}) for k in ('remote_identity', 'provider_identity', 'owner_identity'))
    if any(not isinstance(i, dict) for i in (remote, provider, owner)):
        return False
    if any(any(type(i.get(k)) is not int or i[k] <= 0 for k in ('pid', 'created')) for i in (remote, provider, owner)):
        return False
    if len({i['pid'] for i in (remote, provider, owner)}) != 3:
        return False
    target, result = report.get('owned_target', {}), report.get('result', {})
    if not cross.target_valid(target, provider['pid']) or not phase_qualified(result, target, remote):
        return False
    expected_boundary = {'start': boundary_evidence(target), 'end': boundary_evidence(target, complete=True)}
    if (report.get('capture_boundary') != expected_boundary or
            result['phases'][0].get('capture_boundary') != expected_boundary['end']):
        return False
    first, last = report.get('initial_snapshot', {}), report.get('final_snapshot', {})
    if (first.get('identity') != remote or last.get('identity') != remote or
            type(first.get('tid')) is not int or not 0 < first['tid'] < 2**32 or last.get('tid') != first['tid'] or
            any(s.get('dll_sha256') != evidence.DLL_SHA256 for s in (first, last)) or
            report.get('parser_source') != {'identity': remote, 'tid': first['tid']}):
        return False
    try:
        merge_modules(first.get('modules'), last.get('modules'))
    except (ValueError, TypeError):
        return False
    gate = StopBarrier(remote, report.get('nonce'))
    gate.requested = True
    if not gate.accept(report.get('stop_evidence', {}), report.get('target_alive_at_stop_ack') is True):
        return False
    cleanup, client = report.get('cleanup', {}), report.get('remote_cleanup', {})
    finished = result['phases'][0]['remote_finish']
    owner_cleanup = report.get('owner_cleanup', {})
    consumed = report.get('consumption', {})
    return bool(not report.get('error') and not report.get('recovery_required') and
        not report.get('target_lost_before_stop') and report.get('window_complete') is True and
        report.get('stop_barrier_passed') is True and
        report.get('source_unchanged') is True and report.get('uia_dll_unchanged') is True and
        report.get('input_checkpoints_unchanged') is True and report.get('checkpoint_count', 0) >= 2 and
        report.get('evidence', {}).get('passed') is True and
        report.get('evidence', {}).get('synchronous_chain_verified') is True and
        report.get('evidence', {}).get('require_stack') is True and
        report.get('evidence', {}).get('observation_complete') is True and
        consumed.get('file_count') in (1, 2) and consumed.get('process_status') == [0] * consumed['file_count'] and
        consumed.get('close_status') == [0] * consumed['file_count'] and
        all(cleanup.get(k) is True for k in ('child_exited', 'native_cleanup_confirmed', 'owned_windows_absent',
            'desktop_closed', 'desktop_absent')) and not cleanup.get('terminated') and not cleanup.get('errors') and
        client.get('exited') is True and type(client.get('exitcode')) is int and client['exitcode'] == 0 and
        client.get('terminated') is False and not client.get('errors') and client.get('result') == finished and
        owner_cleanup.get('exited') is True and owner_cleanup.get('exitcode') == 0 and
        owner_cleanup.get('terminated') is False and not owner_cleanup.get('error') and
        owner_cleanup.get('result') == {'target_handle_closed': True} and
        report.get('target_handle_closed') is True and
        all(report.get('process_handles_closed', {}).get(k) is True for k in ('client', 'provider', 'owner')))


def complete_pass(report):
    try:
        return _complete_pass(report)
    except (AttributeError, TypeError, KeyError, ValueError):
        return False


def observe_client_exit(process, deadline):
    """Read-only wait when Stop is unknown; never terminate or reset deadline."""
    result = {'exited': False, 'exitcode': None, 'terminated': False,
              'errors': ['stop_unproven_target_exit_not_authorized']}
    if process is None or not process.pid:
        result['exited'] = True
        return result
    try:
        while process.is_alive() and time.monotonic() < deadline:
            process.join(min(.05, max(0, deadline - time.monotonic())))
        result.update(exited=not process.is_alive(), exitcode=process.exitcode)
    except Exception:
        result['errors'].append('remote_exit_observation_failed')
    if not result['exited']:
        # Same original handle, never a reopened PID. This is a bounded actual
        # follow-up before retaining an unresolved Process for caller recovery.
        try:
            kernel = c.WinDLL('kernel32')
            status = base.call(kernel, 'WaitForSingleObject', base.U32, [base.PTR, base.U32])(
                int(process.sentinel), 2000)
            result['sentinel_wait'] = status
            if status == 0:
                code = base.U32()
                if base.call(kernel, 'GetExitCodeProcess', c.c_int, [base.PTR, c.POINTER(base.U32)])(
                        int(process.sentinel), c.byref(code)):
                    result.update(exited=True, exitcode=code.value)
        except Exception:
            result['errors'].append('remote_sentinel_recheck_failed')
    return result


def read_trace_message(pipe, *, closed=False, stopped=False, complete=False):
    """The client closes this pipe after the proven Stop acknowledgement.

    Its provider result arrives on another channel. Expected EOF must not abort
    that result's collection, while EOF before the full Stop remains a failure.
    """
    if closed:
        return True, None
    try:
        return (False, pipe.recv()) if pipe.poll() else (False, None)
    except (EOFError, BrokenPipeError):
        if stopped and complete:
            return True, None
        raise RuntimeError('trace_channel_closed_before_proven_stop') from None


def run(directory):
    """Single campaign. All controlled target exits pass through stop_barrier."""
    cross.require_sources()
    if os.name != 'nt' or c.sizeof(base.PTR) != 8:
        raise RuntimeError('windows_x64_required')
    exact_dll()
    schemas, sources, initial = manifest(), fingerprints(), base.input_snapshot()
    nonce, desktop_id = uuid.uuid4().hex, 'BridgeSetFocus-' + uuid.uuid4().hex
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    stem, name = 'capture', 'BridgeSetFocus-' + nonce
    path, journal = directory / (stem + '.etl'), directory / 'owner-intent.json'
    report = {'passed': False, 'nonce': nonce, 'phase': PHASE, 'research_only': True,
        'live_client_verified': False, 'foreground_activation_verified': False, 'live_allowance_touched': False,
        'input_checkpoints_unchanged': True, 'checkpoint_count': 0, 'window_complete': False,
        'temporary_files_retained': True, 'fingerprints': sources, 'dll_sha256': evidence.DLL_SHA256,
        'os': base.platform.version(), 'observation_limit': 'Input state sampled, not continuously proven.'}
    ctx, user = mp.get_context('spawn'), c.WinDLL('user32', use_last_error=True)
    client = provider = owner = desktop = held = gate = None
    pipes, target, native, finished, owner_finished = [], {}, {}, {}, {}
    stop_requested = False
    trace_closed = False
    operation = 'prepare'
    capture_requested_at = None
    def alive():
        return held is not None and held.wait(0) == 258
    def accept_stop(message):
        current = alive()
        if gate and gate.accept(message, current):
            report.update(stop_evidence=message, target_alive_at_stop_ack=current)
            report.setdefault('controller_timing', {})['stop_ack'] = time.monotonic()
            return True
        return False
    def stop_barrier():
        """No client join/terminate helper is reachable before this returns."""
        if gate is None or gate.may_exit:
            return True
        gate.observe(alive())
        try:
            owner_pipe.send({'event': 'stop', 'nonce': nonce})
        except (OSError, EOFError):
            pass
        end = time.monotonic() + .4
        while time.monotonic() < end:
            gate.observe(alive())
            try:
                if owner_pipe.poll(.01):
                    message = owner_pipe.recv()
                    if message.get('event') == 'stopped' and accept_stop(message):
                        return True
            except (OSError, EOFError):
                break
        # Stop the only possible future controller before external recovery.
        if owner is not None and owner.is_alive():
            owner.terminate()
            owner.join(.5)
            report['owner_forced_exit'] = True
        if owner is not None and owner.is_alive():
            return False
        if journal.exists() and journal.stat().st_size <= 16384:
            try:
                recorded = json.loads(journal.read_text(encoding='utf-8'))
                if recorded.get('event') == 'stopped' and accept_stop(recorded):
                    return True
            except (OSError, ValueError):
                pass
        before = alive()
        gate.observe(before)
        scope = normal.Scope(name, evidence.PROVIDER, gate.identity['pid'], str(path))
        result = normal.stop_owned_scope(private.controller(), scope)
        message = {'event': 'stopped', 'nonce': nonce, 'identity': gate.identity,
            'reason': 'external_recovery', 'alive_before_stop': before, 'alive_after_stop': alive(), 'stop': result}
        recovery.write_atomic(directory / 'external-recovery.json', message)
        return accept_stop(message)
    try:
        owner_pipe, owner_child = ctx.Pipe()
        pipes += [owner_pipe, owner_child]
        owner = ctx.Process(target=owner_main, args=(owner_child, nonce, name, str(path), str(journal)))
        owner.start()
        owner_child.close()
        ready = cross.receive(owner_pipe, time.monotonic() + 5)
        owner_id = {'pid': owner.pid, 'created': cross.created(c.WinDLL('kernel32'), int(owner.sentinel))}
        if ready != {'event': 'owner_ready', 'nonce': nonce, 'identity': owner_id}:
            raise RuntimeError('owner_ready_mismatch')
        report['owner_identity'] = owner_id
        desktop = base.create_private_desktop(user, desktop_id, initial[0])
        control, client_control = ctx.Pipe()
        relay_host, relay_client = ctx.Pipe()
        trace_pipe, trace_child = ctx.Pipe()
        pipes += [control, client_control, relay_host, relay_client, trace_pipe, trace_child]
        client = ctx.Process(target=client_main, args=(relay_client, client_control, trace_child, desktop_id, nonce))
        # Conservative deadline starts before spawn; the child's own watchdog
        # starts later and is never extended by this calibration.
        client_deadline = time.monotonic() + cross.CLIENT_SECONDS
        client.start()
        client_control.close(); relay_client.close(); trace_child.close()
        ready = cross.receive(control, min(client_deadline - START_MARGIN, time.monotonic() + 5))
        identity = {'pid': client.pid, 'created': cross.created(c.WinDLL('kernel32'), int(client.sentinel))}
        if not owned_ready(ready, identity):
            raise RuntimeError('remote_ready_mismatch')
        held, gate = cross.HeldProcess(identity), StopBarrier(identity, nonce)
        report['remote_identity'] = dict(identity)
        parent, provider_child = ctx.Pipe()
        pipes += [parent, provider_child]
        provider = ctx.Process(target=provider_main, args=(provider_child, desktop_id, relay_host, identity, nonce))
        provider.start()
        provider_child.close(); relay_host.close()
        provider_id = {'pid': provider.pid, 'created': cross.created(c.WinDLL('kernel32'), int(provider.sentinel))}
        report['provider_identity'] = provider_id
        control.send({'provider': provider_id, 'nonce': nonce})
        if cross.receive(control, min(client_deadline - START_MARGIN, time.monotonic() + 3)) != {'bound': True}:
            raise RuntimeError('remote_bind_ack_missing')
        while time.monotonic() < client_deadline:
            report['checkpoint_count'] += 1
            if base.input_snapshot() != initial:
                report['input_checkpoints_unchanged'] = False
                raise RuntimeError('input_state_changed')
            gate.observe(alive())
            if gate.target_lost:
                raise RuntimeError('target_died_before_proven_stop')
            operation = 'provider_read'
            if parent.poll():
                message = parent.recv()
                if 'ready' in message and not target and cross.target_valid(message['ready'], provider.pid):
                    target = message['ready']; report['owned_target'] = dict(target)
                elif message.get('abort_client') == nonce:
                    raise RuntimeError('provider_requested_abort')
                elif 'result' in message:
                    report['result'], native = message['result'], message.get('cleanup', {})
                    break
                else:
                    raise RuntimeError('provider_protocol_mismatch')
            operation = 'trace_read'
            trace_closed, message = read_trace_message(trace_pipe, closed=trace_closed,
                stopped=gate.stopped, complete=report['window_complete'])
            report['trace_channel_closed'] = trace_closed
            if message is not None:
                if message.get('nonce') != nonce or message.get('phase') != PHASE:
                    raise RuntimeError('trace_client_protocol')
                event, snapshot = message.get('event'), message.get('snapshot', {})
                if event in ('start', 'stop'):
                    if (snapshot.get('identity') != identity or snapshot.get('dll_sha256') != evidence.DLL_SHA256 or
                            type(snapshot.get('tid')) is not int or snapshot['tid'] <= 0):
                        raise RuntimeError('trace_snapshot_identity')
                if event == 'start' and target:
                    if message.get('boundary') != boundary_evidence(target):
                        raise RuntimeError('capture_start_boundary_mismatch')
                    merge_modules(snapshot['modules'], snapshot['modules'])
                    gate.start(time.monotonic(), client_deadline)
                    report['initial_snapshot'] = snapshot
                    report['capture_boundary'] = {'start': message['boundary']}
                    recovery.write_atomic(directory / 'controller-intent.json', {'nonce': nonce,
                        'identity': identity, 'owner': owner_id, 'name': name, 'path': str(path), 'phase': PHASE,
                        'capture_boundary': message['boundary']})
                    capture_requested_at = time.monotonic()
                    report.setdefault('controller_timing', {})['capture_request'] = capture_requested_at
                    owner_pipe.send({'event': 'start', 'nonce': nonce, 'identity': identity, 'client_deadline': client_deadline})
                elif event == 'stop' and gate.enabled and not stop_requested:
                    if message.get('boundary') != boundary_evidence(target, complete=True):
                        raise RuntimeError('capture_stop_boundary_mismatch')
                    if snapshot['tid'] != report['initial_snapshot']['tid']:
                        raise RuntimeError('trace_thread_changed')
                    report['final_snapshot'] = snapshot
                    report.setdefault('controller_timing', {})['phase_complete_received'] = time.monotonic()
                    report['capture_boundary']['end'] = message['boundary']
                    merge_modules(report['initial_snapshot']['modules'], snapshot['modules'])
                    stop_requested = True
                    owner_pipe.send({'event': 'stop', 'nonce': nonce})
                elif event == 'abort':
                    raise RuntimeError('client_requested_abort')
                else:
                    raise RuntimeError('trace_client_order')
            operation = 'owner_read'
            if owner_pipe.poll():
                message = owner_pipe.recv()
                if message.get('nonce') != nonce:
                    raise RuntimeError('owner_nonce_mismatch')
                if message.get('event') == 'enabled' and gate.requested and not gate.enabled and message.get('identity') == identity:
                    report.setdefault('controller_timing', {})['enabled_received'] = time.monotonic()
                    gate.enabled = True
                    operation = 'trace_enable_ack'
                    trace_pipe.send({'event': 'enabled', 'nonce': nonce, 'phase': PHASE})
                elif message.get('event') == 'stopped':
                    if not accept_stop(message):
                        raise RuntimeError('stop_not_proven_while_target_alive')
                    report['window_complete'] = stop_requested and message.get('reason') == 'requested'
                    operation = 'trace_stop_ack'
                    trace_pipe.send({'event': 'stopped', 'nonce': nonce, 'phase': PHASE})
                    if not report['window_complete']:
                        raise RuntimeError('capture_window_incomplete')
                else:
                    raise RuntimeError('owner_protocol_mismatch')
            if capture_requested_at is not None and not gate.stopped and time.monotonic() >= capture_requested_at + CAPTURE_SECONDS + .5:
                raise RuntimeError('capture_owner_deadline')
            time.sleep(.005)
        else:
            raise RuntimeError('calibration_client_deadline')
    except BaseException as exc:
        report['error'] = str(exc) if type(exc) is RuntimeError else type(exc).__name__
        report['error_stage'] = operation
    finally:
        safe = False
        try:
            safe = stop_barrier()
        except BaseException as exc:
            report['stop_barrier_error'] = type(exc).__name__
        report['stop_barrier_passed'] = safe
        report['target_lost_before_stop'] = bool(gate and gate.target_lost)
        if safe:
            # This helper can terminate; only use it after the Stop barrier.
            remote = cross.reap_client(client)
        else:
            # No controlled target exit is allowed. Observe its original handle
            # until the frozen watchdog ends it, without resetting its deadline.
            end = max(time.monotonic(), locals().get('client_deadline', time.monotonic())) + 1
            remote = observe_client_exit(client, end)
            report['recovery_required'] = True
        if 'control' in locals():
            try:
                while control.poll():
                    item = control.recv()
                    if item.get('nonce') == nonce and 'client_finished' in item:
                        finished = item['client_finished']
            except (OSError, EOFError):
                pass
        remote['result'] = finished
        report['remote_cleanup'] = remote
        if remote['exited']:
            if 'parent' in locals():
                try:
                    parent.send('stop')
                except (OSError, EOFError):
                    pass
            report['cleanup'] = base.cleanup_owned(provider, user, desktop, desktop_id, target, native)
        else:
            _OWNED_RECOVERY.append((client, provider, owner))
            report['recovery_required'] = True
            report['cleanup'] = {'errors': ['client_exit_unconfirmed'], 'child_exited': False}
            report['cleanup']['desktop_closed'] = bool(desktop and base.call(user, 'CloseDesktop', c.c_int, [base.PTR])(desktop))
        try:
            report['target_handle_closed'] = held.close() if held else False
        except Exception:
            report['target_handle_closed'] = False
        if owner is not None:
            try:
                owner_pipe.send({'event': 'close', 'nonce': nonce})
            except (OSError, EOFError):
                pass
            forced = report.get('owner_forced_exit', False)
            owner_errors = []
            try:
                if owner.pid:
                    owner.join(.5)
                    if owner.is_alive():
                        # This is only the owned controller, never the target.
                        owner.terminate(); owner.join(.5); forced = True
            except Exception:
                owner_errors.append('owner_exit_or_termination_failed')
            try:
                while owner_pipe.poll():
                    message = owner_pipe.recv()
                    if message.get('event') == 'owner_finished' and message.get('nonce') == nonce:
                        owner_finished = message.get('result', {})
            except (OSError, EOFError):
                pass
            report['owner_cleanup'] = {'exited': not owner.is_alive(), 'exitcode': owner.exitcode,
                'terminated': forced, 'result': owner_finished}
            if owner_errors:
                report['owner_cleanup']['error'] = owner_errors
            if owner.is_alive():
                # One additional real wait on the original owned child. The
                # report remains failed even if this later observation succeeds.
                owner.join(.5)
                report['owner_cleanup']['followup_exited'] = not owner.is_alive()
                if owner.is_alive():
                    _OWNED_RECOVERY.append((client, provider, owner))
                    report['recovery_required'] = True
        report['process_handles_closed'] = {}
        for role, process in (('client', client), ('provider', provider), ('owner', owner)):
            closed = False
            try:
                if process is None:
                    closed = True
                elif not process.is_alive():
                    process.close(); closed = True
            except Exception:
                report.setdefault('cleanup', {}).setdefault('errors', []).append(role + '_handle_close_failed')
            report['process_handles_closed'][role] = closed
        for pipe in pipes:
            try:
                pipe.close()
            except Exception:
                report.setdefault('cleanup', {}).setdefault('errors', []).append('pipe_close_failed')
        report['checkpoint_count'] += 1
        try:
            report['input_checkpoints_unchanged'] &= base.input_snapshot() == initial
        except Exception:
            report['input_checkpoints_unchanged'] = False
    if gate and gate.stopped and report['window_complete']:
        try:
            first, last = report['initial_snapshot'], report['final_snapshot']
            modules = merge_modules(first['modules'], last['modules'])
            parsed = evidence.SetFocusEvidence(gate.identity['pid'], first['tid'], schemas, modules)
            consumer = NativeEvidence(parsed)
            consumed = private.consume_files(private.controller(), directory, stem, consumer)
            report['consumption'] = consumed
            report['parser_source'] = {'identity': dict(gate.identity), 'tid': first['tid']}
            completed = (not consumer.callback_error and all(v == 0 for v in consumed['process_status']) and
                         all(v == 0 for v in consumed['close_status']))
            report['evidence'] = parsed.finish(expect_focus=True, observation_complete=completed,
                events_lost=report['stop_evidence']['stop']['events_lost'])
        except BaseException as exc:
            report['evidence'] = {'passed': False, 'error': type(exc).__name__}
    report['source_unchanged'] = sources == fingerprints()
    report['uia_dll_unchanged'] = hashlib.sha256(dll_path().read_bytes()).hexdigest() == evidence.DLL_SHA256
    report['passed'] = complete_pass(report)
    report['state'] = 'synthetic_setfocus_stack_verified' if report['passed'] else 'calibration_not_verified'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-synthetic', action='store_true')
    args = parser.parse_args(argv)
    if not args.run_synthetic:
        print(json.dumps({'state': 'disabled', 'passed': False, 'live_client_verified': False}))
        return 0
    directory = ROOT / '.local/live-acceptance' / ('uia-setfocus-stack-' + uuid.uuid4().hex)
    try:
        report = run(directory)
    except BaseException as exc:
        report = {'passed': False, 'state': 'calibration_not_verified', 'error': type(exc).__name__, 'live_client_verified': False}
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'report.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({'path': str(path), 'passed': report['passed'], 'state': report['state'], 'error': report.get('error')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
