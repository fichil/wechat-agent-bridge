"""Pure synthetic tree/client tests; never imports native COM or pywinauto."""
import copy
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import probe_uia_autofocus_tree as probe


class Fixture:
    def __init__(self):
        self.target = {'pid': 31, 'host': 100, 'a': 101, 'b': 102}
        self.uia = SimpleNamespace(CUIAutomation='legacy', CUIAutomation8='modern',
            IUIAutomation='base', IUIAutomation2='v2', UIA_ValuePatternId=10002,
            IUIAutomationValuePattern='value', UIA_AutomationIdPropertyId=30011,
            UIA_ControlTypePropertyId=30003, UIA_EditControlTypeId=50004, TreeScope_Descendants=4)
        self.calls, self.clients, self.events = [], [], []
        self.value, self.focus, self.count = '', 102, 1
        self.wrong_id, self.write_error, self.activate_false = False, False, False

    def create(self, kind, interface):
        fixture = self

        class Client:
            AutoSetFocus = 1

            def CreatePropertyCondition(self, key, value):
                return key, value

            def CreateAndCondition(self, left, right):
                return left, right

            def ElementFromHandle(self, hwnd):
                fixture.calls.append(('element', id(self), hwnd))
                return Root(self) if hwnd == 100 else Edit(self, False)

        class Root:
            CurrentProcessId, CurrentNativeWindowHandle = 31, 100

            def __init__(self, client):
                self.client = client

            def GetRuntimeId(self):
                return [42, 100]

            def FindAll(self, scope, condition):
                fixture.calls.append(('findall', id(self.client), scope, condition))
                assert scope == 4 and condition == ((30011, '101'), (30003, 50004))
                return SimpleNamespace(Length=fixture.count, GetElement=lambda index: Edit(self.client, True))

        class Edit:
            CurrentProcessId, CurrentNativeWindowHandle, CurrentControlType = 31, 101, 50004

            def __init__(self, client, from_tree):
                self.client, self.from_tree = client, from_tree

            @property
            def CurrentAutomationId(self):
                return 'wrong' if fixture.wrong_id else '101'

            def GetRuntimeId(self):
                return [42, 101]

            def GetCurrentPattern(self, pattern_id):
                assert self.from_tree and pattern_id == 10002
                fixture.calls.append(('pattern', id(self.client), 'tree'))
                return Pattern(self.client)

        class Pattern:
            CurrentIsReadOnly = 0

            def __init__(self, client):
                self.client = client

            def QueryInterface(self, interface):
                assert interface == 'value'
                return self

            @property
            def CurrentValue(self):
                fixture.calls.append(('read', id(self.client), kind))
                return fixture.value

            def SetValue(self, text):
                fixture.calls.append(('write', id(self.client), kind, self.client.AutoSetFocus))
                fixture.value = text
                if fixture.write_error:
                    raise RuntimeError('synthetic_unknown')
                if self.client.AutoSetFocus or fixture.activate_false:
                    fixture.focus, fixture.events = 101, [[102, 'kill'], [101, 'set']]

        client = Client()
        self.clients.append(client)
        return client

    def exchange(self, action, name):
        if action == 'prepare':
            self.value, self.focus, self.events = '', 102, []
        if action == 'complete':
            return {'focus': self.focus, 'focus_events': self.events, 'event_overflow': False,
                    'native_full_readback_equal': self.value == probe.candidate.FIXED_DRAFT}
        return {'focus': self.focus, 'focus_events': self.events}

    def phase(self, name):
        return probe.tree_phase(self.create, self.uia, self.target, self.exchange, name)

    def result(self):
        return {'target': self.target, 'mta_verified': True,
                'phases': [self.phase(name) for name in probe.PHASES]}


class TreeAutoFocusContractTests(unittest.TestCase):
    def test_four_tree_paths_use_real_candidate_and_distinct_writer_objects(self):
        fixture = Fixture()
        result = fixture.result()
        self.assertTrue(probe.source_qualified(result))
        writes = [item for item in fixture.calls if item[0] == 'write']
        self.assertEqual([item[3] for item in writes], [1, 1, 0, 0])
        self.assertEqual(len({item[1] for item in writes}), 4)
        for writer in writes:
            self.assertIn(('pattern', writer[1], 'tree'), fixture.calls)
            self.assertTrue(any(item[:2] == ('findall', writer[1]) for item in fixture.calls))
        for phase in result['phases'][2:]:
            self.assertEqual(phase['candidate_result']['status'], 'readback_confirmed')
            self.assertEqual(phase['synthetic_claim_count'], 1)
        self.assertEqual(probe.candidate.INPUT_AUTOMATION_ID, 'chat_input_field')

    def test_old_reader_is_a_different_live_object_used_at_six_checkpoints(self):
        fixture = Fixture()
        result = fixture.phase(probe.PHASES[3])
        self.assertEqual(result['legacy_reader_checks'], 6)
        self.assertTrue(result['legacy_reader_final_readback_equal'])
        writer = next(item[1] for item in fixture.calls if item[0] == 'write')
        legacy_readers = {item[1] for item in fixture.calls if item[0] == 'read' and item[2] == 'legacy'}
        self.assertEqual(len(legacy_readers), 1)
        self.assertNotIn(writer, legacy_readers)

    def test_missing_or_ambiguous_tree_cannot_write(self):
        for count in (0, 2, True):
            fixture = Fixture()
            fixture.count = count
            result = fixture.phase(probe.PHASES[2])
            self.assertEqual(result['candidate_result']['status'], 'failed')
            self.assertFalse(any(item[0] == 'write' for item in fixture.calls))
            self.assertEqual(probe.candidate.INPUT_AUTOMATION_ID, 'chat_input_field')

    def test_actual_owned_automation_id_must_be_verified_before_replacement(self):
        fixture = Fixture()
        fixture.wrong_id = True
        with self.assertRaisesRegex(RuntimeError, 'synthetic_edit_automation_id_not_verified'):
            fixture.phase(probe.PHASES[2])
        self.assertFalse(any(item[0] == 'write' for item in fixture.calls))
        self.assertEqual(probe.candidate.INPUT_AUTOMATION_ID, 'chat_input_field')

    def test_unknown_result_is_not_retried_or_misreported(self):
        fixture = Fixture()
        fixture.write_error = True
        result = fixture.phase(probe.PHASES[2])
        self.assertEqual(result['candidate_result']['status'], 'unknown')
        self.assertTrue(result['attempted'])
        self.assertFalse(result['identity_stable'])
        self.assertEqual(sum(item[0] == 'write' for item in fixture.calls), 1)

    def test_focus_even_after_exact_value_set_rejects_false_phase(self):
        fixture = Fixture()
        fixture.activate_false = True
        result = fixture.phase(probe.PHASES[2])
        self.assertEqual(result['candidate_result']['status'], 'unknown')
        self.assertEqual(result['candidate_result']['reason'], 'candidate_checkpoint_rejected')
        self.assertTrue(result['native_full_readback_equal'])
        self.assertFalse(result['uia_full_readback_equal'])

    def test_source_needs_positive_controls_and_the_retained_reader_evidence(self):
        result = Fixture().result()
        for index, change in ((0, {'focus_events': []}), (1, {'focus': 102}),
                              (2, {'configured_auto_set_focus': 1}),
                              (3, {'legacy_reader_checks': 0}), (3, {'legacy_reader_retained': False}),
                              (3, {'legacy_reader_final_readback_equal': False}),
                              (2, {'real_candidate_used': False}), (2, {'synthetic_claim_count': 2})):
            changed = copy.deepcopy(result)
            changed['phases'][index].update(change)
            self.assertFalse(probe.source_qualified(changed), change)

    def test_temporary_module_attributes_restore_even_after_exception(self):
        module = SimpleNamespace(first=1, second=2)
        with self.assertRaises(RuntimeError):
            with probe.replace_attributes(module, first=3, second=4):
                raise RuntimeError('synthetic')
        self.assertEqual(vars(module), {'first': 1, 'second': 2})

    def test_changed_frozen_base_cannot_run(self):
        with patch.object(probe.hashlib, 'sha256', return_value=Mock(hexdigest=lambda: 'different')):
            with self.assertRaisesRegex(RuntimeError, 'frozen_autofocus_base_changed'):
                probe.require_base()

    def test_default_stays_disabled_and_no_live_target_arguments_exist(self):
        with patch.object(probe, 'run', side_effect=AssertionError('native forbidden')) as native:
            with patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(probe.main([]), 0)
            native.assert_not_called()
        for argument in ('--pid', '--hwnd', '--target-evidence', '--account', '--operator-idle'):
            with patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit):
                probe.main([argument, '123'])


if __name__ == '__main__':
    unittest.main()
