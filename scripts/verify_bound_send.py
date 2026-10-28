"""Explicit fixed-message acceptance experiment, not a production send port.

Uses the already verified account/process and manually selected test group.
The fixed campaign slot is durably consumed before staging. Invoke is attempted
once; unknown results never authorize retries. No keyboard, mouse or clipboard.
An observed matching echo is deliberately NOT verified own-message identity.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import platform
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import probe_background_draft as original
from scripts.probe_bound_message_labels import collect_labels
from scripts.uia_client_bootstrap import prepare_client_elements, read_pattern_context, require_safe_context
from wechat_agent_bridge.acceptance import AcceptanceLedger, FIXED_TEXT
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.adapters.accessibility_gate import DLL_SHA256, VERSION


def require_fixed_send_capability(fingerprint):
    """The old experiment is withdrawn after its actual Press path failed.

    No live implementation has qualified yet. Keep inspection and exact test
    draft cleanup available without reopening an ineffective send experiment.
    """
    if fingerprint.get('version') == VERSION and fingerprint.get('dll_sha256') == DLL_SHA256:
        raise ConsoleError('send_accessible_press_unsupported')
    raise ConsoleError('background_send_implementation_unverified')


def target_matches(expected, current):
    if (current.get('identity_verified') is not True or current.get('target_index') != 0 or
            current.get('selected_row_verified') is not True or current.get('value_readonly') is not False or
            any(current.get(k) != expected.get(k) or not expected.get(k)
                for k in ('input_runtime_id', 'session_runtime_id'))):
        raise ConsoleError('fixed_send_target_changed')


def fixed_transaction(port, checkpoint, reserve, mark_invoke,*,text=FIXED_TEXT):
    """Mockable experiment. No provider, automatic recovery write or resend."""
    staged, invoked = False, False
    result = dict(state='failed', draft_attempted=False, send_attempted=False,
                  own_identity_verified=False, recipient_delivery_verified=False)
    def check(phase):
        checkpoint(phase)
        port.verify_target()
    try:
        check('preflight')
        if port.draft() != '':
            raise ConsoleError('existing_draft_or_unknown')
        port.require_send_capability()
        before = port.capture()
        if any(row['label'] == text for row in before['rows']):
            raise ConsoleError('fixed_text_already_present_no_retry')
        check('before_claim')
        port.guard_patterns()
        reserve()  # Budget and task state must commit before any mutation.
        check('before_set_value')
        if port.draft() != '':
            raise ConsoleError('existing_draft_or_unknown')
        staged = True
        port.set_value(text)
        check('after_set_value')
        if port.draft() != text:
            raise ConsoleError('fixed_text_readback_mismatch')
        port.guard_patterns()
        if not port.send_enabled():
            raise ConsoleError('send_control_not_enabled')
        check('before_invoke')
        if port.draft() != text:
            raise ConsoleError('draft_changed_before_send')
        mark_invoke()  # Persist unknown BEFORE allowing the COM invocation.
        invoked = True
        port.invoke()
        check('after_invoke')
        before_ids = {tuple(row['runtime_id']) for row in before['rows']}
        until = time.monotonic() + 5
        while True:
            check('message_readback')
            after = port.capture()
            empty = port.draft() == ''
            matches = [row for row in after['rows'] if row['label'] == text and
                       tuple(row['runtime_id']) not in before_ids]
            check('after_message_readback')
            if empty and len(matches) == 1:
                result.update(state='message_echo_observed', draft_empty_after=True,
                              new_exact_echo_count=1, before_count=len(before['rows']), after_count=len(after['rows']),
                              echo_runtime_id=matches[0]['runtime_id'],
                              reason='sender_semantics_still_unverified')
                break
            if time.monotonic() >= until:
                raise ConsoleError('fixed_send_echo_missing_or_ambiguous')
            time.sleep(.1)
    except Exception as exc:
        result.update(state='unknown' if invoked else 'failed',
                      reason=str(exc) if isinstance(exc, ConsoleError) else 'fixed_send_operation_failed')
    result.update(draft_attempted=staged, send_attempted=invoked)
    return result


def clear_test_draft(port, checkpoint, mark_cleanup,*,text=FIXED_TEXT):
    """Only this task's exact fixed text, with fresh checks; never invoke Send."""
    attempted = False
    try:
        checkpoint('cleanup_preflight'); port.verify_target()
        current = port.draft()
        if current == '':
            return dict(state='draft_already_empty', cleanup_attempted=False, send_attempted=False)
        if current != text:
            raise ConsoleError('non_test_draft_preserved')
        port.guard_patterns()
        port.verify_target()
        if port.draft() != text:
            raise ConsoleError('non_test_draft_preserved')
        mark_cleanup()
        attempted = True
        port.set_value('')
        checkpoint('after_test_cleanup'); port.verify_target()
        if port.draft() != '':
            raise ConsoleError('test_cleanup_readback_missing')
        return dict(state='fixed_draft_cleared', cleanup_attempted=True, send_attempted=False,
                    draft_empty=True)
    except Exception as exc:
        return dict(state='unknown' if attempted else 'failed', cleanup_attempted=attempted,
                    send_attempted=False, reason=str(exc) if isinstance(exc, ConsoleError)
                    else 'test_cleanup_failed')


class UIAPort:
    def __init__(self, fp, names, expected):
        from scripts.probe_bound_uia_context import prepare_send_element, check_send_element
        self.fp, self.names, self.expected = fp, names, expected
        self._check_send_element = check_send_element
        self.prepared = prepare_client_elements(fp, expected)
        self.client, self.root, self.element, self.pattern, self.legacy_loaded, self.uia = self.prepared
        self.send_bundle = prepare_send_element(self.prepared, fp)
        from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
        self.backend = WindowsUIA()
        self.contexts = []

    def metadata(self):
        return self.backend.target_control_probe(self.fp['pid'], self.fp['hwnd'], self.names)

    def verify_target(self):
        target_matches(self.expected, self.metadata())
        self._check_send_element(self.send_bundle, self.fp, self.uia)

    def guard_patterns(self):
        import ctypes as c
        from scripts.uia_context_evidence import inspect_context
        from scripts.uia_context_native import SelfMemory
        self.client.require_background_mode()
        value = read_pattern_context(self.client._client, self.element, self.pattern)
        require_safe_context(value)
        memory = SelfMemory()
        try:
            invoke = inspect_context(memory, memory.image,
                c.cast(self.client._client, c.c_void_p).value,
                c.cast(self.send_bundle[1], c.c_void_p).value,
                c.cast(self.send_bundle[2], c.c_void_p).value, False, pattern_kind='invoke')
            memory.validate_source()
        finally:
            memory.close()
        require_safe_context(invoke)
        self.contexts.append(dict(value=value, invoke=invoke))

    def draft(self):
        if self.pattern.CurrentIsReadOnly:
            raise ConsoleError('input_readonly')
        return self.pattern.CurrentValue

    def capture(self):
        return collect_labels(self.client._client, self.root, self.uia, self.fp['pid'])

    def send_enabled(self): return bool(self.send_bundle[1].CurrentIsEnabled)
    def set_value(self, value): self.pattern.SetValue(value)
    def require_send_capability(self): require_fixed_send_capability(self.fp)
    def invoke(self):
        self.require_send_capability()
        self.send_bundle[2].Invoke()


def worker(pipe, fp, names, expected, execute, inspect_after=False, clear_draft=False,*,port_factory=UIAPort,text=FIXED_TEXT):
    sys.coinit_flags = 0
    try:
        port = port_factory(fp, names, expected)
        port.checkpoint=lambda phase: original._exchange(pipe,'checkpoint',phase)
        current = port.metadata()
        if inspect_after or clear_draft:
            target_matches(expected, current)
        else:
            original.check_draft_target(expected, current)
        original._exchange(pipe, 'verified', current)
        port.guard_patterns()
        if clear_draft:
            result = clear_test_draft(port,
                lambda phase: original._exchange(pipe, 'checkpoint', phase),
                lambda: original._exchange(pipe, 'cleanup'),text=text)
            result['draft_attempted'] = False
        elif inspect_after:
            rows = port.capture()['rows']
            draft = port.draft()
            port.verify_target()
            original._exchange(pipe, 'checkpoint', 'after_attempt_readonly')
            result = dict(state='attempt_inspected', draft_attempted=False, send_attempted=False,
                          draft_empty=draft == '', draft_is_fixed=draft == text,
                          draft_is_other=draft not in ('', text), loaded_count=len(rows),
                          exact_fixed_count=sum(row['label'] == text for row in rows),
                          containing_fixed_count=sum(text in row['label'] for row in rows),
                          own_identity_verified=False)
        elif execute:
            result = fixed_transaction(port,
                lambda phase: original._exchange(pipe, 'checkpoint', phase),
                lambda: original._exchange(pipe, 'reserve'),
                lambda: original._exchange(pipe, 'invoke'),text=text)
        else:
            result = dict(state='preflight_verified', draft_attempted=False, send_attempted=False)
        result.update(contexts=port.contexts, legacy_loaded_before_client=port.legacy_loaded)
        if hasattr(port,'native_evidence'): result['native_evidence']=port.native_evidence
        pipe.send(dict(kind='result', value=result))
    except Exception as exc:
        try:
            pipe.send(dict(kind='error', value=str(exc) if isinstance(exc, ConsoleError) else 'fixed_send_worker_failed'))
        except (OSError, EOFError):
            pass
    finally:
        pipe.close()


def sources():
    paths = ('scripts/verify_bound_send.py', 'scripts/probe_bound_message_labels.py',
             'wechat_agent_bridge/adapters/bound_read.py',
             'scripts/probe_bound_uia_context.py', 'wechat_agent_bridge/acceptance.py')
    return dict(original.helper_fingerprints(), **{
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths})


def run(args,*,candidate=None):
    from scripts.probe_bound_uia_context import require_context_canary
    from wechat_agent_bridge.adapters.accessibility_gate import NativeGate, AccessibilityGate
    from wechat_agent_bridge.adapters.background import snapshot, unchanged
    from wechat_agent_bridge.adapters.desktop_events import DesktopEventObserver
    from wechat_agent_bridge.adapters.virtual_desktop import (VirtualDesktopReader, validate_initial,
        unchanged_snapshot,validate_current_target,unchanged_current_target)
    from wechat_agent_bridge.console_store import ConsoleStore
    from wechat_agent_bridge.protection import DPAPIProtector
    if not (args.allow_accessibility_patch and args.account and args.group and args.private and
            args.target_evidence and args.synthetic_evidence and args.virtual_desktop):
        raise ConsoleError('explicit_account_targets_and_evidence_required')
    if args.group == args.private:
        raise ConsoleError('acceptance_target_ambiguous')
    current_desktop=getattr(args,'current_desktop_background',False)
    if current_desktop and candidate is None:
        raise ConsoleError('current_desktop_requires_reviewed_candidate')
    if candidate is not None and getattr(candidate,'desktop_scope','separated_desktops') != (
            'current_desktop_background' if current_desktop else 'separated_desktops'):
        raise ConsoleError('candidate_desktop_scope_mismatch')
    require_context_canary(args.synthetic_evidence)
    uia_sha = original.require_canary()
    evidence_path = Path(args.target_evidence).resolve()
    if evidence_path.parent != original.DIRECTORY.resolve() or evidence_path.stat().st_size > 1_000_000:
        raise ConsoleError('local_target_evidence_required')
    evidence = json.loads(evidence_path.read_text(encoding='utf-8-sig'))
    store = ConsoleStore(original.DIRECTORY / 'gate.sqlite3', DPAPIProtector())
    ledger = AcceptanceLedger(original.DIRECTORY / 'quota.sqlite3')
    lock = original.RunLock(original.DIRECTORY / 'autofocus-draft.lock')
    native = gate = observer = desktop = process = parent = child = None
    claimed = invoked = cleanup_attempted = False
    request = ('fixed-background-' if candidate is None else candidate.name+'-') + uuid.uuid4().hex
    send_slot='fixed' if candidate is None else candidate.slot
    def source_snapshot(): return dict(sources(),**(candidate.sources() if candidate is not None else {}))
    report = dict(kind='bound_fixed_send_experiment', state='failed', draft_attempted=False,
                  send_attempted=False, api_called=False, production_enabled=False,
                  os=platform.version(), uia_sha256=uia_sha, sources=source_snapshot(), cleanup_errors=[])
    if candidate is not None:
        report['kind']=candidate.name
        report['candidate_case']=getattr(candidate,'case','legacy')
    report['desktop_scope']='current_desktop_background' if current_desktop else 'separated_desktops'
    started = time.monotonic()
    def persist(): store.put('acceptance_fixed_send', request, report)
    try:
        lock.acquire()
        binding = store.get('binding', 'account', {})
        target_scope=getattr(candidate,'target_scope',None) if candidate is not None else None
        if target_scope is None:
            expected = original.validate_binding(binding, evidence, args.account, binding.get('fingerprint', {}))
            names=[args.group,args.private]; report['target_kind']='group'
        else:
            expected=target_scope.resolve(binding,evidence,args.account,binding.get('fingerprint',{}),store,original.DIRECTORY)
            names=target_scope.names; report.update(target_scope.metadata())
        if any(store.get(k,'pending') for k in ('patch','draft_probe','thread_hook','development_navigation','development_window_restore','development_exact_search')):
            raise ConsoleError('prior_recovery_required')
        if args.run_fixed_send and send_slot in ledger.status()['reserved_send_slots']:
            raise ConsoleError('acceptance_attempt_already_reserved')
        if (args.inspect_after_attempt or args.clear_test_draft) and send_slot not in ledger.status()['reserved_send_slots']:
            raise ConsoleError('prior_fixed_attempt_required')
        native = NativeGate(binding['fingerprint']['hwnd'])
        fp = native.fingerprint()
        if asdict(fp) != binding['fingerprint']:
            raise ConsoleError('same_process_verified_binding_required')
        report['fingerprint']=asdict(fp)
        if not (args.inspect_after_attempt or args.clear_test_draft):
            (require_fixed_send_capability if candidate is None else candidate.require_capability)(asdict(fp))
        baseline = snapshot(fp.pid)
        unchanged(baseline, baseline)
        desktop = VirtualDesktopReader()
        target_desktop=dict(hwnd=fp.hwnd, **desktop.query(fp.hwnd))
        desktop_binding=(validate_current_target(target_desktop) if current_desktop else
            validate_initial(target_desktop,dict(hwnd=baseline.foreground, **desktop.query(baseline.foreground))))
        if desktop_binding['target']['desktop_id'] != args.virtual_desktop:
            raise ConsoleError('taskview_target_desktop_changed')
        report['virtual_desktop_binding']=desktop_binding
        observer = DesktopEventObserver(fp.pid).__enter__()
        def check():
            if time.monotonic() - started >= 40:
                raise ConsoleError('fixed_send_deadline')
            if native.fingerprint() != fp or store.get('binding', 'account', {}) != binding:
                raise ConsoleError('fixed_send_account_identity_changed')
            if target_scope is not None: target_scope.check(store)
            observer.check()
            now = snapshot(fp.pid)
            unchanged(baseline, now)
            target_desktop=dict(hwnd=fp.hwnd, **desktop.query(fp.hwnd))
            if current_desktop:
                unchanged_current_target(desktop_binding,target_desktop)
            else:
                unchanged_snapshot(desktop_binding,target_desktop,
                                   dict(hwnd=now.foreground, **desktop.query(now.foreground)))
            return True
        gate = AccessibilityGate(native, allowed=True, save=lambda x: store.put('patch', 'pending', x))
        report['patch'] = gate.enable(fp, check)
        context = mp.get_context('spawn')
        parent, child = context.Pipe()
        process = context.Process(target=worker if candidate is None else candidate.worker,
            args=(child, asdict(fp), names, expected, args.run_fixed_send,
                  args.inspect_after_attempt, args.clear_test_draft))
        process.start(); child.close()
        verified = False
        while True:
            check()
            if not parent.poll(.03):
                if not process.is_alive(): raise ConsoleError('fixed_send_worker_disconnected')
                continue
            message = parent.recv()
            kind, value = message.get('kind'), message.get('value')
            if kind == 'verified' and not verified:
                if args.inspect_after_attempt or args.clear_test_draft:
                    target_matches(expected, value)
                else:
                    original.check_draft_target(expected, value)
                verified = True
                report['target_verified'] = True
                parent.send({'continue': True})
            elif kind == 'checkpoint' and verified:
                observer.set_phase(str(value))
                parent.send({'continue': check()})
            elif kind == 'reserve' and verified and args.run_fixed_send and not claimed:
                check()
                if candidate is not None and candidate.grant(ledger)!=send_slot:
                    raise ConsoleError('candidate_slot_grant_mismatch')
                ledger.reserve('send', request, send_slot)
                claimed = True
                report.update(state='unknown', draft_attempted=True, budget_reserved=True)
                persist()
                parent.send({'continue': True})
            elif kind == 'invoke' and claimed and not invoked:
                check()
                invoked = True
                report.update(state='unknown', send_attempted=True)
                persist()
                observer.set_phase('invoke_send')
                parent.send({'continue': True})
            elif kind == 'cleanup' and verified and args.clear_test_draft and not cleanup_attempted:
                check()
                cleanup_attempted = True
                report.update(state='unknown', cleanup_attempted=True)
                persist()
                observer.set_phase('clear_test_draft')
                parent.send({'continue': True})
            elif kind == 'result' and verified:
                if value.get('send_attempted') and not invoked:
                    raise ConsoleError('fixed_send_result_without_reservation')
                report['worker'] = value
                report['state'] = value['state']
                if value.get('reason'):
                    report['reason'] = value['reason']
                until = time.monotonic() + .5
                while time.monotonic() < until:
                    check(); time.sleep(.02)
                report['desktop_unchanged'] = True
                break
            elif kind == 'error':
                raise ConsoleError(value)
            else:
                raise ConsoleError('fixed_send_protocol_mismatch')
    except BaseException as exc:
        report.update(state='unknown' if invoked or cleanup_attempted else 'failed',
            reason=str(exc) if isinstance(exc, ConsoleError) else
                   'operator_cancelled' if isinstance(exc, KeyboardInterrupt) else 'fixed_send_coordinator_failed')
    finally:
        if parent:
            try: parent.send({'continue': False})
            except (OSError, EOFError): pass
        if process and process.pid:
            process.join(1)
            if process.is_alive(): process.terminate(); process.join(2)
            report['worker_exited'] = not process.is_alive()
            if process.exitcode != 0: report['cleanup_errors'].append('worker_exit_unconfirmed')
        if gate:
            try:
                report['restore'] = gate.restore()
                report['patch_restored'] = report['restore']['state'] in ('restored', 'unchanged')
                if not report['patch_restored']: report['cleanup_errors'].append('patch_restore_unconfirmed')
            except BaseException:
                report['cleanup_errors'].append('patch_restore_failed')
        if observer:
            try: observer.close()
            except BaseException: observer.evidence.failed = True
            report['events_observed'] = len(observer.evidence.events)
            report['desktop_events'] = list(observer.evidence.events)
            if observer.evidence.events or observer.evidence.failed:
                report.update(state='unknown' if invoked or cleanup_attempted else 'failed', desktop_unchanged=False)
        for label, resource in (('desktop', desktop), ('native', native), ('parent_pipe', parent), ('child_pipe', child)):
            if resource:
                try: resource.close()
                except BaseException: report['cleanup_errors'].append(label + '_close_failed')
        report['sources_unchanged'] = source_snapshot() == report['sources']
        if not report['sources_unchanged']: report['cleanup_errors'].append('sources_changed')
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        report['quota'] = ledger.status()
        if report['cleanup_errors']: report['state'] = 'recovery_required'
        try:
            persist()
            if not store.get('patch', 'pending'):
                lock.release()
        finally:
            ledger.close(); store.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--preflight', action='store_true')
    modes.add_argument('--run-fixed-send', action='store_true')
    modes.add_argument('--inspect-after-attempt', action='store_true')
    modes.add_argument('--clear-test-draft', action='store_true')
    parser.add_argument('--allow-accessibility-patch', action='store_true')
    for name in ('account', 'group', 'private', 'target-evidence', 'synthetic-evidence', 'virtual-desktop'):
        parser.add_argument('--' + name)
    args = parser.parse_args(argv)
    if not (args.preflight or args.run_fixed_send or args.inspect_after_attempt or args.clear_test_draft):
        print(json.dumps(dict(state='disabled', send_enabled=False)))
        return 0
    try:
        report = run(args)
    except Exception as exc:
        report = dict(state='failed', reason=str(exc) if isinstance(exc, ConsoleError)
                      else 'fixed_send_preparation_failed', send_attempted=False)
    path = original.DIRECTORY / ('fixed-send-' + uuid.uuid4().hex + '.json')
    with path.open('x', encoding='utf-8') as stream: json.dump(report, stream, indent=2)
    print(json.dumps(dict(path=str(path), state=report['state'], reason=report.get('reason'),
                         send_attempted=report.get('send_attempted'), patch_restored=report.get('patch_restored'))))
    return 0 if report['state'] in ('preflight_verified', 'message_echo_observed', 'attempt_inspected',
                                    'fixed_draft_cleared', 'draft_already_empty') else 1


if __name__ == '__main__':
    raise SystemExit(main())
