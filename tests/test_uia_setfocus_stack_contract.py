"""Pure contracts. No desktop, COM, ETW, process launch or paid API."""
import contextlib
import copy
import ctypes as c
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import probe_uia_setfocus_stack as probe
from scripts import test_focus_etw as etw


IDENTITY = {'pid': 51, 'created': 1234}
MODULES = [(0x10000000, 0x500000, 'UIAutomationCore.dll')]
TARGET = {'pid': 31, 'tid': 44, 'host': 100, 'a': 101, 'b': 102}


def stop_message(**changes):
    result = {'event': 'stopped', 'nonce': 'owned', 'identity': dict(IDENTITY),
        'alive_before_stop': True, 'alive_after_stop': True, 'reason': 'requested',
        'stop': {'actual_name_and_file_verified': True, 'stop_status': 0,
                 'query_after': 4201, 'events_lost': 0, 'absent': True}}
    result.update(changes)
    return result


def fixture():
    target = {'pid': 31, 'tid': 44, 'host': 100, 'a': 101, 'b': 102}
    finished = {'cleanup': {k: True for k in ('com_uninitialized', 'provider_handle_closed',
                                             'desktop_restored', 'desktop_closed')}}
    phase = {'phase': probe.PHASE, 'focus_before': 102, 'focus': 101, 'focus_events': [[101, 'set']],
        'pattern_path': 'root_findall_edit_id', 'synthetic_input_id': '101', 'configured_auto_set_focus': 1,
        'real_candidate_used': False, 'remote_client_pid': 51, 'remote_client_created': 1234,
        'remote_finish': copy.deepcopy(finished)}
    phase.update({k: True for k in ('fresh_client', 'attempted', 'uia_full_readback_equal',
        'native_full_readback_equal', 'identity_stable', 'remote_exit_before_provider_cleanup',
        'relay_client_handle_closed', 'remote_mta_verified')})
    phase['capture_boundary'] = probe.boundary_evidence(target, complete=True)
    snapshot = {'identity': dict(IDENTITY), 'tid': 77, 'dll_sha256': probe.evidence.DLL_SHA256,
                'modules': copy.deepcopy(MODULES)}
    return {'nonce': 'owned', 'remote_identity': dict(IDENTITY), 'provider_identity': {'pid': 31, 'created': 99},
        'owner_identity': {'pid': 71, 'created': 555}, 'owned_target': target,
        'result': {'target': target, 'mta_verified': True, 'phases': [phase]},
        'capture_boundary': {'start': probe.boundary_evidence(target),
                             'end': probe.boundary_evidence(target, complete=True)},
        'initial_snapshot': copy.deepcopy(snapshot), 'final_snapshot': copy.deepcopy(snapshot),
        'parser_source': {'identity': dict(IDENTITY), 'tid': 77}, 'stop_evidence': stop_message(),
        'target_alive_at_stop_ack': True, 'stop_barrier_passed': True, 'window_complete': True,
        'source_unchanged': True, 'uia_dll_unchanged': True, 'input_checkpoints_unchanged': True,
        'checkpoint_count': 3, 'evidence': {'passed': True, 'synchronous_chain_verified': True,
            'require_stack': True, 'observation_complete': True},
        'consumption': {'file_count': 1, 'process_status': [0], 'close_status': [0]},
        'cleanup': {k: True for k in ('child_exited', 'native_cleanup_confirmed', 'owned_windows_absent',
                                    'desktop_closed', 'desktop_absent')},
        'remote_cleanup': {'exited': True, 'exitcode': 0, 'terminated': False, 'result': finished},
        'owner_cleanup': {'exited': True, 'exitcode': 0, 'terminated': False,
                          'result': {'target_handle_closed': True}},
        'target_handle_closed': True, 'process_handles_closed': {'client': True, 'provider': True, 'owner': True}}


class BarrierTests(unittest.TestCase):
    def gate(self):
        item = probe.StopBarrier(IDENTITY, 'owned')
        item.start(10, 20)
        return item

    def test_disabled_default_does_not_call_run(self):
        with patch.object(probe, 'run', side_effect=AssertionError('native')), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(probe.main([]), 0)
        self.assertEqual(json.loads(out.getvalue())['state'], 'disabled')

    def test_external_pid_argument_not_accepted(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            probe.main(['--pid', '51'])

    def test_client_ready_matches_creation_identity_and_mta(self):
        self.assertTrue(probe.owned_ready({'ready': IDENTITY, 'mta_verified': True}, IDENTITY))
        self.assertFalse(probe.owned_ready({'ready': {'pid': 51, 'created': 99}, 'mta_verified': True}, IDENTITY))
        self.assertFalse(probe.owned_ready({'ready': IDENTITY, 'mta_verified': 1}, IDENTITY))

    def test_no_trace_can_exit_but_unrequested_stop_is_not_evidence(self):
        gate = probe.StopBarrier(IDENTITY, 'owned')
        self.assertTrue(gate.may_exit)
        self.assertFalse(gate.accept(stop_message(), True))

    def test_start_requires_capture_plus_cleanup_margin_and_is_single_use(self):
        gate = probe.StopBarrier(IDENTITY, 'owned')
        self.assertGreaterEqual(probe.START_MARGIN - probe.CAPTURE_SECONDS, 3)
        boundary = 20 - probe.START_MARGIN
        with self.assertRaises(RuntimeError):
            gate.start(boundary + .001, 20)
        self.assertFalse(gate.requested)
        gate.start(boundary, 20)
        self.assertFalse(gate.may_exit)
        with self.assertRaises(RuntimeError):
            gate.start(boundary, 20)

    def test_actual_stop_while_alive_releases_target(self):
        gate = self.gate()
        self.assertTrue(gate.accept(stop_message(), True))
        self.assertTrue(gate.may_exit)
        gate.observe(False)  # Normal exit after Stop is admitted.
        self.assertFalse(gate.target_lost)

    def test_absence_alone_never_releases_target(self):
        gate = self.gate()
        self.assertFalse(gate.accept(stop_message(stop={'query_before': 4201, 'absent': True}), True))
        self.assertFalse(gate.may_exit)

    def test_target_death_before_stop_is_sticky_even_if_pid_reappears(self):
        gate = self.gate()
        gate.observe(False)
        self.assertFalse(gate.accept(stop_message(), True))
        self.assertFalse(gate.may_exit)

    def test_stop_identity_nonce_and_each_alive_observation_required(self):
        for change in ({'nonce': 'other'}, {'identity': {'pid': 51, 'created': 1235}},
                       {'event': 'enabled'}, {'alive_before_stop': False}, {'alive_after_stop': False}):
            with self.subTest(change=change):
                gate = self.gate()
                self.assertFalse(gate.accept(stop_message(**change), True))
                self.assertFalse(gate.may_exit)
        self.assertFalse(self.gate().accept(stop_message(), False))

    def test_loss_failed_stop_or_failed_query_never_release(self):
        for key, value in (('events_lost', 1), ('stop_status', 5), ('query_after', 0),
                           ('actual_name_and_file_verified', False)):
            message = stop_message()
            message['stop'][key] = value
            self.assertFalse(self.gate().accept(message, True))

    def test_unknown_stop_wait_only_observes_owned_client_exit(self):
        class Child:
            pid, exitcode = 51, None
            def is_alive(self):
                return self.exitcode is None
            def join(self, timeout):
                self.exitcode = 43
            def terminate(self):
                raise AssertionError('target_termination_not_authorized')
        status = probe.observe_client_exit(Child(), probe.time.monotonic() + 1)
        self.assertTrue(status['exited'])
        self.assertEqual(status['exitcode'], 43)
        self.assertFalse(status['terminated'])
        self.assertTrue(status['errors'])

    def test_single_phase_loop_restores_original_branch_tuple(self):
        events = []
        class Pipe:
            def __init__(self):
                self.replies = []
            def send(self, message):
                events.append(message['event'])
                event = 'enabled' if message['event'] == 'start' else 'stopped'
                self.replies.append({'event': event, 'nonce': 'owned', 'phase': probe.PHASE})
            def poll(self, _timeout):
                return bool(self.replies)
            def recv(self):
                return self.replies.pop(0)
            def close(self):
                events.append('closed')
        def original(_create, _bindings, _target, exchange, name, _records):
            self.assertEqual(probe.tree.PHASES, probe.ORIGINAL_PHASES)
            self.assertEqual(name, probe.ORIGINAL_PHASES[1])
            for action in ('prepare', 'check', 'check', 'complete'):
                exchange(action, name)
            return {'phase': name}
        def client(*_args):
            self.assertEqual(probe.tree.PHASES, (probe.PHASE,))
            return probe.tree.tree_phase(None, None, TARGET,
                lambda *_: {'focus': TARGET['b'], 'focus_events': []}, probe.PHASE)
        with (patch.object(probe.tree, 'tree_phase', original),
              patch.object(probe.cross, 'client_main', client),
              patch.object(probe.c, 'WinDLL'), patch.object(probe, 'self_snapshot', return_value={})):
            probe.client_main(None, None, Pipe(), 'private', 'owned')
        self.assertEqual(events, ['start', 'stop', 'closed'])
        self.assertEqual(probe.tree.PHASES, probe.ORIGINAL_PHASES)


class TraceChannelTests(unittest.TestCase):
    def test_closed_after_complete_stop_does_not_discard_provider_result(self):
        for stage in ('poll', 'recv'):
            for error in (EOFError, BrokenPipeError):
                pipe = SimpleNamespace(poll=lambda: True, recv=lambda: None)
                with self.subTest(stage=stage, error=error), patch.object(pipe, stage, side_effect=error):
                    self.assertEqual(probe.read_trace_message(pipe, stopped=True, complete=True), (True, None))

    def test_early_close_is_failure_for_every_incomplete_stop_state(self):
        for stopped, complete in ((False, False), (True, False), (False, True)):
            pipe = SimpleNamespace(poll=lambda: True, recv=lambda: None)
            with self.subTest(stopped=stopped, complete=complete), patch.object(pipe, 'recv', side_effect=EOFError):
                with self.assertRaisesRegex(RuntimeError, '^trace_channel_closed_before_proven_stop$'):
                    probe.read_trace_message(pipe, stopped=stopped, complete=complete)

    def test_closed_channel_is_not_repolled(self):
        pipe = SimpleNamespace(poll=lambda: self.fail('closed channel polled'))
        self.assertEqual(probe.read_trace_message(pipe, closed=True), (True, None))

    def test_messages_and_unrelated_errors_are_not_hidden(self):
        pipe = SimpleNamespace(poll=lambda: True, recv=lambda: {'event': 'unexpected'})
        self.assertEqual(probe.read_trace_message(pipe), (False, {'event': 'unexpected'}))
        with patch.object(pipe, 'poll', side_effect=OSError('not EOF')), self.assertRaises(OSError):
            probe.read_trace_message(pipe, stopped=True, complete=True)


class CaptureWindowTests(unittest.TestCase):
    def ack(self, window, action, value=None, phase=probe.PHASE):
        window.before(action, phase)
        return window.acknowledge(value if value is not None else {'focus': 102, 'focus_events': []})

    def test_only_second_checked_ack_starts_capture(self):
        window = probe.CaptureWindow(probe.PHASE, TARGET)
        self.assertIsNone(self.ack(window, 'prepare', {'focus': 102}))
        self.assertIsNone(self.ack(window, 'check'))
        self.assertEqual(self.ack(window, 'check'), 'start')
        self.assertEqual(window.evidence(), probe.boundary_evidence(TARGET))
        self.assertEqual(self.ack(window, 'complete', {'focus': 101, 'focus_events': [[101, 'set']]}), 'stop')
        self.assertEqual(window.finish(), probe.boundary_evidence(TARGET, complete=True))

    def test_wrong_phase_rejected_before_forwarding(self):
        with self.assertRaises(RuntimeError):
            probe.CaptureWindow('legacy_default_tree', TARGET)
        window = probe.CaptureWindow(probe.PHASE, TARGET)
        with self.assertRaises(RuntimeError):
            window.before('prepare', 'legacy_default_tree')
        self.assertEqual(window.acknowledged, [])

    def test_missing_extra_and_reordered_check_rejected(self):
        bad_sequences = [('check',), ('complete',), ('prepare', 'prepare'),
            ('prepare', 'complete'), ('prepare', 'check', 'complete'),
            ('prepare', 'check', 'check', 'check'),
            ('prepare', 'check', 'check', 'complete', 'complete')]
        for actions in bad_sequences:
            with self.subTest(actions=actions), self.assertRaises(RuntimeError):
                window = probe.CaptureWindow(probe.PHASE, TARGET)
                for action in actions:
                    self.ack(window, action)

    def test_second_check_requires_empty_events_and_correct_focus(self):
        for value in ({'focus': 101, 'focus_events': []}, {'focus': 102},
                      {'focus': 102, 'focus_events': [[101, 'set']]},
                      {'focus': 102, 'focus_events': [], 'event_overflow': True}, {'error': 'failed'}):
            window = probe.CaptureWindow(probe.PHASE, TARGET)
            self.ack(window, 'prepare')
            self.ack(window, 'check')
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                self.ack(window, 'check', value)
            self.assertTrue(window.failed)
            with self.assertRaises(RuntimeError):
                window.evidence()

    def test_unacknowledged_or_partial_sequence_cannot_finish(self):
        window = probe.CaptureWindow(probe.PHASE, TARGET)
        window.before('prepare', probe.PHASE)
        with self.assertRaises(RuntimeError):
            window.before('check', probe.PHASE)
        for count in range(4):
            window = probe.CaptureWindow(probe.PHASE, TARGET)
            for action in window.ORDER[:count]:
                self.ack(window, action)
            with self.subTest(count=count), self.assertRaises(RuntimeError):
                window.finish()

    def actual_phase(self, *, enable_failure=False, bad_second=False):
        """Execute the frozen tree_phase, replacing only its native COM edges."""
        calls, messages, reports = [], [], []
        class Pattern:
            CurrentIsReadOnly = False
            CurrentValue = ''
            def SetValue(self, value):
                calls.append(('setter', id(self)))
                self.CurrentValue = value
        pattern = Pattern()
        expected = SimpleNamespace(input_runtime_id=(42, 101), root_runtime_id=(42, 100))
        root = SimpleNamespace(GetRuntimeId=lambda: expected.root_runtime_id)
        edit = object()
        class Pipe:
            def __init__(self):
                self.replies = []
            def send(self, message):
                messages.append(message)
                calls.append((message['event'], None))
                if message['event'] == 'start' and enable_failure:
                    self.replies.append(RuntimeError('synthetic_enable_failure'))
                else:
                    event = 'enabled' if message['event'] == 'start' else 'stopped'
                    self.replies.append({'event': event, 'nonce': 'owned', 'phase': probe.PHASE})
            def poll(self, _timeout):
                return bool(self.replies)
            def recv(self):
                value = self.replies.pop(0)
                if isinstance(value, Exception):
                    raise value
                calls.append(('ack_' + value['event'], None))
                return value
            def close(self):
                pass
        def binding(*_args):
            calls.append(('binding', None))
            return expected, '101'
        def tree_pattern(*_args):
            calls.append(('pattern', id(pattern)))
            return root, None, edit, pattern
        check_count = 0
        def exchange(action, _name):
            nonlocal check_count
            if action == 'check':
                check_count += 1
                calls.append(('check_' + str(check_count), None))
                return {'focus': 102, 'focus_events': [[101, 'set']] if bad_second and check_count == 2 else []}
            calls.append((action, None))
            return ({'focus': 102} if action == 'prepare' else
                    {'focus': 101, 'focus_events': [[101, 'set']], 'native_full_readback_equal': True})
        modern, interface = object(), object()
        def create(kind, **kwargs):
            self.assertIs(kind, modern)
            self.assertIs(kwargs['interface'], interface)
            return SimpleNamespace(AutoSetFocus=1)
        def client(*_args):
            report = probe.tree.tree_phase(create, SimpleNamespace(CUIAutomation8=modern,
                IUIAutomation2=interface), TARGET, exchange, probe.PHASE, reports)
            self.assertIs(report, reports[0])
        error = None
        with (patch.object(probe.tree, 'binding', binding), patch.object(probe.tree, 'tree_pattern', tree_pattern),
              patch.object(probe.base, 'identity', return_value=expected.input_runtime_id),
              patch.object(probe.cross, 'client_main', client), patch.object(probe.c, 'WinDLL'),
              patch.object(probe, 'self_snapshot', return_value={})):
            try:
                probe.client_main(None, None, Pipe(), 'private', 'owned')
            except RuntimeError as exc:
                error = str(exc)
        return calls, messages, reports, pattern, error

    def test_real_frozen_phase_uses_same_pattern_after_enabled_ack(self):
        calls, messages, reports, pattern, error = self.actual_phase()
        self.assertIsNone(error)
        names = [name for name, _ in calls]
        self.assertLess(names.index('check_1'), names.index('pattern'))
        self.assertLess(names.index('pattern'), names.index('check_2'))
        self.assertEqual(names[names.index('check_2'):names.index('setter') + 1],
                         ['check_2', 'start', 'ack_enabled', 'setter'])
        self.assertEqual([item for item in calls if item[0] == 'setter'], [('setter', id(pattern))])
        self.assertEqual(messages[0]['boundary'], probe.boundary_evidence(TARGET))
        self.assertEqual(reports[0]['capture_boundary'], probe.boundary_evidence(TARGET, complete=True))

    def test_bad_second_ack_does_not_start_or_write(self):
        calls, messages, _reports, _pattern, error = self.actual_phase(bad_second=True)
        self.assertEqual(error, 'capture_prewrite_focus_changed')
        self.assertEqual(messages, [])
        self.assertNotIn('setter', [name for name, _ in calls])

    def test_enable_failure_cannot_return_to_setter(self):
        calls, messages, _reports, _pattern, error = self.actual_phase(enable_failure=True)
        self.assertEqual(error, 'synthetic_enable_failure')
        self.assertEqual([m['event'] for m in messages], ['start', 'abort'])
        self.assertNotIn('setter', [name for name, _ in calls])


class EvidenceBoundaryTests(unittest.TestCase):
    def test_module_extension_is_bounded_and_stable(self):
        final = MODULES + [(0x20000000, 4096, 'New.dll')]
        self.assertEqual(probe.merge_modules(MODULES, final)[0][2], 'uiautomationcore.dll')
        for invalid in ([], [(0x20000000, 0x500000, 'UIAutomationCore.dll')],
                        [(0x10000000, 0x500001, 'UIAutomationCore.dll')],
                        MODULES + [(0x10000001, 20, 'Overlap.dll')]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                probe.merge_modules(MODULES, invalid)

    def test_stack_reader_skips_match_id_and_rejects_duplicate(self):
        stack = (c.c_uint64 * 3)(999, 0x10000001, 0x10000002)
        items = (etw.Extended * 2)()
        items[0].kind, items[0].size, items[0].data = 6, 24, c.addressof(stack)
        record = SimpleNamespace(extended_count=1, extended=items)
        self.assertEqual(probe.read_stack(record), [0x10000001, 0x10000002])
        items[1].kind, items[1].size, items[1].data = 6, 24, c.addressof(stack)
        record.extended_count = 2
        with self.assertRaises(ValueError):
            probe.read_stack(record)

    def test_bad_stack_boundary_rejected_before_pointer_read(self):
        for size in (0, 8, 15, 17, 528):
            items = (etw.Extended * 1)()
            items[0].kind, items[0].size, items[0].data = 6, size, 1
            with (patch.object(probe.c, 'string_at', side_effect=AssertionError('pointer_read')),
                    self.assertRaises(ValueError)):
                probe.read_stack(SimpleNamespace(extended_count=1, extended=items))

    def test_foreign_header_does_not_read_extensions_or_payload(self):
        schemas = {probe.evidence.ENTRY: {'id': 335, 'payload_empty': True},
                   probe.evidence.EXIT: {'id': 336, 'payload_empty': True}}
        parsed = probe.evidence.SetFocusEvidence(51, 77, schemas, MODULES)
        native = probe.NativeEvidence(parsed)
        record = etw.Record()
        record.header.provider[:] = probe.evidence.PROVIDER
        record.header.pid, record.header.tid = 52, 77
        record.header.descriptor = etw.Descriptor.from_buffer_copy(probe.evidence.ENTRY)
        record.data, record.extended_count = 1, 65535
        with patch.object(probe, 'read_stack', side_effect=AssertionError('stack_read')):
            native.consume(c.pointer(record))
        self.assertTrue(parsed.failed)
        self.assertFalse(native.callback_error)


class QualificationTests(unittest.TestCase):
    def test_complete_positive_control(self):
        self.assertTrue(probe.complete_pass(fixture()))

    def test_no_pass_without_all_cleanup_and_target_binding(self):
        mutations = [
            lambda r: r['remote_identity'].update(created=999),
            lambda r: r['provider_identity'].update(pid=99),
            lambda r: r['owner_identity'].update(pid=51),
            lambda r: r['stop_evidence'].update(alive_after_stop=False),
            lambda r: r['stop_evidence'].update(identity={'pid': 51, 'created': 999}),
            lambda r: r['stop_evidence'].update(nonce='other'),
            lambda r: r.update(target_lost_before_stop=True),
            lambda r: r.update(window_complete=False),
            lambda r: r.update(stop_barrier_passed=False),
            lambda r: r['remote_cleanup'].update(result={}),
            lambda r: r['remote_cleanup'].update(terminated=True),
            lambda r: r['remote_cleanup'].update(exitcode=43),
            lambda r: r['owner_cleanup'].update(result={}),
            lambda r: r['owner_cleanup'].update(exited=False),
            lambda r: r['owner_cleanup'].update(terminated=True),
            lambda r: r['process_handles_closed'].update(owner=False),
            lambda r: r['cleanup'].update(desktop_absent=False),
            lambda r: r['consumption'].update(close_status=[5]),
            lambda r: r['evidence'].update(require_stack=False),
            lambda r: r['evidence'].update(synchronous_chain_verified=False),
            lambda r: r['evidence'].update(passed=False),
            lambda r: r['final_snapshot'].update(tid=99),
            lambda r: r['parser_source'].update(tid=99),
            lambda r: r.update(source_unchanged=False),
            lambda r: r.pop('capture_boundary'),
            lambda r: r['capture_boundary']['start'].update(version='prepare_ack_v0'),
            lambda r: r['capture_boundary']['end'].update(checks_before_start=1),
            lambda r: r['result']['phases'][0].pop('capture_boundary'),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                report = fixture()
                mutate(report)
                self.assertFalse(probe.complete_pass(report))

    def test_no_pass_for_legacy_or_no_focus_substitute(self):
        for change in ({'phase': 'legacy_default_tree'}, {'configured_auto_set_focus': 0},
                       {'real_candidate_used': True}, {'focus': 102}, {'focus_events': []}):
            report = fixture()
            report['result']['phases'][0].update(change)
            self.assertFalse(probe.complete_pass(report))

    def test_malformed_reports_fail_closed(self):
        for invalid in (None, [], {'result': None}, {'remote_identity': None}):
            self.assertFalse(probe.complete_pass(invalid))


if __name__ == '__main__':
    unittest.main()
