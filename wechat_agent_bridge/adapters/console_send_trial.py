"""Opt-in console integration of the reviewed, exact-build button helper.

No client code changes: the same recovery-tested helper and UIA FALSE context
are used. Every send first performs mode-zero load/inspection/unload against
its current objects. Production profile tables are deliberately untouched.
"""
import multiprocessing as mp
import time

from ..console_models import ConsoleError
from ..send_outcomes import PREWRITE_WAIT_REASONS


def require_cleanup(report):
    cleanup = report.get('cleanup', {})
    if (cleanup.get('owner_exitcode') != 0 or cleanup.get('unhooked') != 1 or
            cleanup.get('drained') != 1 or cleanup.get('errors') != [] or
            any(cleanup.get(k) is not True for k in ('post_unhook_delivered',
                'post_unhook_no_callback', 'helper_unloaded', 'mapping_handles_closed'))):
        raise ConsoleError('console_helper_recovery_unconfirmed')


def readonly_input_interruption(report):
    """Only a proven no-call, fully unloaded mode-zero interruption is retryable."""
    try:
        require_cleanup(report)
    except ConsoleError:
        return False
    return (report.get('qt_method_called') is False and report.get('send_invoked') is False and
            report.get('guard_reason') == 2 and report.get('metadata_checked') is True)


def defer_prewrite_interruption(report, *, staged, invoked, pending):
    if (report.get('status') != 'failed' or report.get('attempted') is not False or
            staged or invoked or pending or report.get('worker_exited') is not True or
            report.get('recovery_required') or report.get('reason') not in PREWRITE_WAIT_REASONS):
        return report
    try:
        for preflight in report.get('preflights', ()):
            require_cleanup(preflight)
        if report.get('preflight'):
            require_cleanup(report['preflight'])
    except ConsoleError:
        return report
    return dict(report, status='deferred', no_write_verified=True)


def _worker(pipe, fp, name, expected, evidence, text):
    import sys
    sys.coinit_flags = 0
    from scripts import probe_bound_thread_hook as hook
    from scripts.verify_bound_signal_send import HookPort
    from scripts.verify_bound_send import UIAPort, fixed_transaction, target_matches
    from scripts.probe_background_draft import _exchange
    from .accessibility_gate import NativeGate, Fingerprint
    from .runtime_accessibility import ExactProfile, ReadOnlyProcess, BoundedMemory, interface_for_id
    from scripts.probe_send_accessible import inspect_interface, inspect_session_guard
    native = reader = probe = closing = None
    result = None
    def checkpoint(phase):
        return _exchange(pipe, 'checkpoint', phase)
    try:
        build = hook.require_recovery(evidence, bound_send=True)
        class Port(HookPort):
            def __init__(self):
                UIAPort.__init__(self, fp, [name], expected)
                self.text, self.native_evidence, self.qualified = text, {}, False
                self.checkpoint = checkpoint

            def metadata(self):
                return self.backend.current_group_target(fp['pid'], fp['hwnd'], [name])

            def verify_target(self):
                current = self.metadata()
                target_matches(expected, current)
                if current.get('group_header_runtime_id') != expected.get('group_header_runtime_id'):
                    raise ConsoleError('bound_console_group_changed')
                self._check_send_element(self.send_bundle, fp, self.uia)

            def require_send_capability(self):
                if not self.qualified or hook.require_recovery(evidence, bound_send=True) != build:
                    raise ConsoleError('console_helper_current_preflight_required')

        port = Port()
        checkpoint('preflight'); port.verify_target(); port.guard_patterns()
        if port.draft() != '':
            raise ConsoleError('existing_draft_or_unknown')
        fingerprint = Fingerprint(**fp)
        native = NativeGate(fingerprint.hwnd)
        if native.fingerprint() != fingerprint:
            raise ConsoleError('console_helper_process_changed')
        profile = ExactProfile(native.path.read_bytes())
        reader = ReadOnlyProcess(fingerprint, native.fingerprint)
        memory = BoundedMemory(reader.read, reader.check, max_bytes=32768)
        sys.path.insert(0, str(hook.ROOT/'.local/static-tools'))
        runtimes = [expected['input_runtime_id'], list(port.send_bundle[3]), expected['session_runtime_id']]
        inspect_interface(memory, fingerprint, profile, runtimes[1], inspect_connections=True)
        inspect_session_guard(memory, fingerprint, profile, runtimes[2])
        widgets = []
        for runtime in runtimes:
            iface = interface_for_id(memory, fingerprint.module_base, runtime)
            weak = memory.number(iface+8); control = memory.number(weak)
            if not control or memory.number(control+4, '<i') == 0:
                raise ConsoleError('console_helper_widget_destroyed')
            widgets.append(memory.number(weak+8))
        def preflight_check():
            checkpoint('readonly_helper'); port.verify_target(); memory.recheck()
            if port.draft() != '':
                raise ConsoleError('existing_draft_or_unknown')
        preflights = []
        for attempt in range(2):
            # Wait for a brief input pause; never suppress input, focus or hooks.
            from .background import input_timestamp
            idle_until = time.monotonic()+4
            previous, quiet_since = input_timestamp(), time.monotonic()
            while time.monotonic()-quiet_since < .75:
                checkpoint('waiting_for_idle')
                now = input_timestamp()
                if now != previous:
                    previous, quiet_since = now, time.monotonic()
                if time.monotonic() >= idle_until:
                    raise ConsoleError('console_send_waiting_for_idle')
                time.sleep(.05)
            preflight_check()
            probe = hook.BoundProbe(fingerprint, native, preflight_check, send_candidate=True)
            failure = None
            try:
                probe.run(runtimes, widgets, before_dispatch=preflight_check)
            except ConsoleError as exc:
                failure = exc
            finally:
                closing, probe = probe, None
                closing.close()
            preflight = closing.report; preflights.append(preflight)
            if failure is None:
                break
            if attempt or not readonly_input_interruption(preflight):
                raise failure
        require_cleanup(preflight)
        if (preflight.get('qt_method_called') is not False or preflight.get('send_invoked') is not False or
                any(preflight.get(k) is not True for k in ('metadata_checked', 'selected', 'gui_thread_match'))):
            raise ConsoleError('console_helper_preflight_unverified')
        port.qualified = True
        checkpoint('preflight_complete')
        if text is None:
            result = dict(status='preflight_verified', attempted=False, preflight=preflight, preflights=preflights)
        else:
            transaction = fixed_transaction(port, checkpoint,
                lambda: _exchange(pipe, 'draft_claim'), lambda: _exchange(pipe, 'invoke_claim'), text=text)
            if port.native_evidence:
                require_cleanup(port.native_evidence)
            success = transaction['state'] == 'message_echo_observed'
            result = dict(status='ui_confirmed' if success else
                          'unknown' if transaction['draft_attempted'] else 'failed',
                          attempted=transaction['draft_attempted'], transaction=transaction,
                          preflight=preflight, preflights=preflights, native=port.native_evidence)
        checkpoint('complete')
    except Exception as exc:
        import traceback
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        result = dict(status='failed', attempted=False, reason=str(exc) if isinstance(exc, ConsoleError)
                      else 'console_helper_'+type(exc).__name__+':'+str(frame.lineno))
        if closing is not None:
            result['preflight'] = closing.report
    finally:
        if probe:
            try: probe.close()
            except Exception: result = dict(status='unknown', attempted=True, reason='console_helper_recovery_unconfirmed')
        if reader: reader.close()
        if native: native.close()
        try: pipe.send(dict(kind='result', value=result))
        finally: pipe.close()


class ConsoleSendTrial:
    def __init__(self, fp, name, expected, evidence, guard, store):
        self.fp, self.name, self.expected = fp, name, expected
        self.evidence, self.guard, self.store = str(evidence), guard, store
        self.process = None
        self.controller_created = None
        self.build = None

    def owns_pending(self, record):
        return bool(self.process and self.process.is_alive() and self.controller_created and
            record.get('fingerprint') == self.fp and record.get('controller_pid') == self.process.pid and
            record.get('controller_created') == self.controller_created and record.get('build') == self.build and
            record.get('mode') in ('readonly_inspection', 'bound_send'))

    def run(self, text=None, request_id=None, checkpoint=lambda: None):
        from .background import snapshot, unchanged, input_timestamp
        from .desktop_events import DesktopEventObserver
        if text is not None and (not request_id or not text.strip() or len(text) > 500):
            raise ConsoleError('invalid_reply_output')
        self.guard(); checkpoint()
        from scripts.probe_bound_thread_hook import require_recovery
        self.build = require_recovery(self.evidence, bound_send=True)
        if self.store.get('thread_hook', 'pending'):
            raise ConsoleError('bound_hook_prior_recovery_required')
        if request_id and self.store.get('console_send', request_id):
            raise ConsoleError('console_send_request_already_attempted')
        # Wait before installing observation: foreground events from an ongoing
        # human click must not be mistaken for the yet-unstarted operation.
        idle_until = time.monotonic()+15
        previous, quiet_since = input_timestamp(), time.monotonic()
        while time.monotonic()-quiet_since < 1:
            checkpoint(); self.guard()
            now = input_timestamp()
            if now != previous:
                previous, quiet_since = now, time.monotonic()
            if time.monotonic() >= idle_until:
                return dict(status='deferred', attempted=False, no_write_verified=True,
                            reason='console_send_waiting_for_idle', worker_exited=True)
            time.sleep(.05)
        baseline = snapshot(self.fp['pid']); unchanged(baseline, baseline)
        ctx = mp.get_context('spawn'); pipe, child = ctx.Pipe()
        process = ctx.Process(target=_worker, args=(child, self.fp, self.name,
            self.expected, self.evidence, text), daemon=True)
        staged = invoked = False
        report = dict(status='failed', attempted=False)
        started = time.monotonic()
        with DesktopEventObserver(self.fp['pid']) as observer:
            try:
                process.start(); child.close()
                # Held multiprocessing handle; never reopen an arbitrary PID.
                import ctypes as c
                kernel = c.WinDLL('kernel32', use_last_error=True)
                kernel.GetProcessTimes.argtypes = [c.c_void_p]*5
                kernel.GetProcessTimes.restype = c.c_int
                times = [c.c_uint64() for _ in range(4)]
                if not kernel.GetProcessTimes(int(process.sentinel), *(c.byref(t) for t in times)):
                    raise ConsoleError('console_worker_identity_unavailable')
                self.process, self.controller_created = process, times[0].value
                while time.monotonic()-started < 40:
                    if not pipe.poll(.05):
                        if not process.is_alive():
                            raise ConsoleError('console_send_worker_exited')
                        checkpoint(); self.guard(); observer.check(); unchanged(baseline, snapshot(self.fp['pid']))
                        continue
                    message = pipe.recv(); kind = message.get('kind')
                    if kind == 'result':
                        report = message['value']; break
                    checkpoint(); self.guard(); observer.check(); unchanged(baseline, snapshot(self.fp['pid']))
                    if kind == 'draft_claim':
                        if text is None or staged: raise ConsoleError('console_send_protocol_invalid')
                        staged = True
                        self.store.put('console_send', request_id, dict(status='unknown', draft_attempted=True,
                            invoke_attempted=False, fingerprint=self.fp))
                    elif kind == 'invoke_claim':
                        if not staged or invoked: raise ConsoleError('console_send_protocol_invalid')
                        invoked = True
                        self.store.put('console_send', request_id, dict(status='unknown', draft_attempted=True,
                            invoke_attempted=True, fingerprint=self.fp))
                    elif kind != 'checkpoint':
                        raise ConsoleError('console_send_protocol_invalid')
                    observer.set_phase(message.get('value') or kind)
                    pipe.send({'continue': True})
                else:
                    raise ConsoleError('console_send_timeout')
                observer.check(); unchanged(baseline, snapshot(self.fp['pid'])); checkpoint(); self.guard()
            except Exception as exc:
                try: pipe.send({'continue': False})
                except Exception: pass
                report = dict(status='unknown' if staged else 'failed', attempted=staged,
                    reason=str(exc) if isinstance(exc, ConsoleError) else 'console_send_worker_failed')
            finally:
                if process.pid:
                    process.join(5)
                    if process.is_alive():
                        process.terminate(); process.join(2)
                        report = dict(status='unknown' if staged else 'failed', attempted=staged,
                                      reason='console_send_worker_timeout_recovery_required')
                pipe.close(); child.close()
                report['events_observed'] = len(observer.evidence.events)
        if staged and report['status'] not in ('ui_confirmed', 'unknown'):
            report.update(status='unknown', attempted=True)
        if self.store.get('thread_hook', 'pending'):
            report.update(status='unknown' if staged else 'failed', attempted=staged,
                          recovery_required=True)
        report.update(elapsed=round(time.monotonic()-started, 3), worker_exited=not process.is_alive())
        # No staging is allowed until the parent's durable draft_claim handshake.
        if text is not None:
            report = defer_prewrite_interruption(report, staged=staged, invoked=invoked,
                pending=self.store.get('thread_hook', 'pending'))
        self.process = None
        process.close()
        self.store.put('console_send' if request_id else 'console_send_preflight', request_id or 'latest', report)
        return report
