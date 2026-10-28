"""Synthetic-only contracts for the unregistered no-focus draft candidate."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from wechat_agent_bridge.adapters import uia_draft_candidate as candidate


BINDINGS = SimpleNamespace(UIA_AutomationIdPropertyId=30011, UIA_ControlTypePropertyId=30003,
    UIA_EditControlTypeId=50004, TreeScope_Descendants=4, UIA_ValuePatternId=10002,
    IUIAutomationValuePattern=object())


class Pattern:
    def __init__(self, operations):
        self.operations = operations
        self.CurrentIsReadOnly = 0
        self.value = ""
        self.write_error = False
        self.mismatched_readback = False
        self.writes = []

    @property
    def CurrentValue(self):
        self.operations.append("read_value")
        return self.value

    def SetValue(self, text):
        self.operations.append("set_value")
        self.writes.append(text)
        self.value = "different" if self.mismatched_readback else text
        if self.write_error:
            raise RuntimeError("must not leak private provider details")


class Element:
    def __init__(self, operations, runtime, hwnd=0):
        self.operations, self.runtime = operations, runtime
        self.CurrentProcessId, self.CurrentNativeWindowHandle = 321, hwnd
        self.CurrentAutomationId, self.CurrentControlType = "chat_input_field", 50004
        self.pattern = Pattern(operations)
        self.GetCurrentPattern = Mock(return_value=Mock(QueryInterface=Mock(return_value=self.pattern)))

    def GetRuntimeId(self):
        return self.runtime


class Automation:
    def __init__(self):
        self.operations = []
        self.AutoSetFocus = 1
        self.root = Element(self.operations, (42, 1), 123)
        self.input = Element(self.operations, (42, 2))
        self.matches = SimpleNamespace(Length=1, GetElement=Mock(return_value=self.input))
        self.root.FindAll = Mock(return_value=self.matches)
        self.CreatePropertyCondition = Mock(side_effect=lambda key, value: (key, value))
        self.CreateAndCondition = Mock(side_effect=lambda first, second: (first, second))
        self.ElementFromHandle = Mock(return_value=self.root)


class DraftCandidateTests(unittest.TestCase):
    def setUp(self):
        self.raw = Automation()
        self.target = candidate.DraftTarget(123, 321, (42, 1), (42, 2))
        self.factory = Mock(return_value=self.raw)
        self.phases = []
        self.checkpoint = Mock(side_effect=lambda phase: self.phases.append(phase) or True)
        self.claim = Mock(side_effect=lambda: self.raw.operations.append("claim") or True)

    def run_candidate(self, **overrides):
        options = dict(enabled=True, checkpoint=self.checkpoint, claim=self.claim,
                       client_factory=self.factory, bindings=BINDINGS)
        options.update(overrides)
        return candidate.write_fixed_draft(self.target, **options)

    def test_disabled_by_default_without_client_discovery_or_callbacks(self):
        result = candidate.write_fixed_draft(self.target, client_factory=self.factory,
                                             checkpoint=self.checkpoint, claim=self.claim)
        self.assertEqual(result['status'], 'disabled')
        self.factory.assert_not_called()
        self.checkpoint.assert_not_called()
        self.claim.assert_not_called()

    def test_one_fixed_draft_from_same_new_client_and_full_readback(self):
        result = self.run_candidate()
        self.assertEqual(result['status'], 'readback_confirmed')
        self.assertTrue(result['attempted'])
        self.assertTrue(result['claim_confirmed'])
        self.assertTrue(result['readback_equal'])
        self.assertFalse(result['live_compatible'])
        self.assertEqual(self.raw.AutoSetFocus, 0)
        self.factory.assert_called_once_with()
        self.raw.ElementFromHandle.assert_called_once_with(123)
        self.raw.root.FindAll.assert_called_once_with(4, ((30011, 'chat_input_field'), (30003, 50004)))
        self.raw.input.GetCurrentPattern.assert_called_once_with(10002)
        self.raw.input.GetCurrentPattern.return_value.QueryInterface.assert_called_once_with(
            BINDINGS.IUIAutomationValuePattern)
        self.assertEqual(self.raw.input.pattern.writes, [candidate.FIXED_DRAFT])
        self.assertLess(self.raw.operations.index('claim'), self.raw.operations.index('set_value'))
        self.assertEqual(self.phases, ['before_discovery', 'after_discovery', 'after_read', 'before_claim',
                                       'before_write', 'commit_write', 'after_write', 'after_readback'])

    def test_root_process_hwnd_or_runtime_mismatch_prevents_input_query(self):
        for field, value in (('CurrentProcessId', 999), ('CurrentNativeWindowHandle', 124), ('runtime', (42, 9))):
            with self.subTest(field=field):
                self.setUp()
                setattr(self.raw.root, field, value)
                result = self.run_candidate()
                self.assertEqual(result['status'], 'failed')
                self.raw.root.FindAll.assert_not_called()
                self.claim.assert_not_called()

    def test_guard_receives_exact_pattern_before_claim_and_before_write(self):
        def guard(raw,element,pattern):
            self.assertIs(raw,self.raw)
            self.assertIs(element,self.raw.input)
            self.assertIs(pattern,self.raw.input.pattern)
            self.raw.operations.append('context_guard')
            return True
        result=self.run_candidate(pattern_guard=guard)
        self.assertEqual(result['status'],'readback_confirmed')
        actions=self.raw.operations
        guards=[i for i,a in enumerate(actions) if a=='context_guard']
        self.assertEqual(len(guards),2)
        self.assertLess(guards[0],actions.index('claim'))
        self.assertLess(actions.index('claim'),guards[1])
        self.assertLess(guards[1],actions.index('set_value'))

    def test_first_pattern_guard_failure_has_no_claim_or_write(self):
        result=self.run_candidate(pattern_guard=lambda *_:False)
        self.assertEqual(result['reason'],'candidate_pattern_context_rejected')
        self.claim.assert_not_called()
        self.assertEqual(self.raw.input.pattern.writes,[])

    def test_pattern_guard_change_after_claim_does_not_write_or_retry(self):
        guard=Mock(side_effect=[True,False])
        result=self.run_candidate(pattern_guard=guard)
        self.assertTrue(result['claim_confirmed'])
        self.assertFalse(result['attempted'])
        self.assertEqual(self.raw.input.pattern.writes,[])
        self.claim.assert_called_once()

    def test_pattern_guard_error_does_not_leak_private_details(self):
        result=self.run_candidate(pattern_guard=Mock(side_effect=RuntimeError('private details')))
        self.assertEqual(result['reason'],'candidate_pattern_context_rejected')
        self.claim.assert_not_called()
        self.assertEqual(self.raw.input.pattern.writes,[])

    def test_input_identity_mismatch_prevents_pattern_and_value_access(self):
        for field, value in (('CurrentProcessId', 999), ('runtime', (42, 9)),
                             ('CurrentAutomationId', 'other'), ('CurrentControlType', 50000)):
            with self.subTest(field=field):
                self.setUp()
                setattr(self.raw.input, field, value)
                result = self.run_candidate()
                self.assertEqual(result['status'], 'failed')
                self.raw.input.GetCurrentPattern.assert_not_called()
                self.assertNotIn('read_value', self.raw.operations)
                self.claim.assert_not_called()

    def test_missing_duplicate_or_invalid_match_count_is_rejected(self):
        for count in (0, 2, True):
            self.raw.matches.Length = count
            self.assertEqual(self.run_candidate()['reason'], 'candidate_input_ambiguous_or_missing')
        self.raw.matches.GetElement.assert_not_called()
        self.claim.assert_not_called()

    def test_existing_or_unknown_draft_never_consumes_claim_or_gets_overwritten(self):
        for draft in ('user draft', None):
            self.raw.input.pattern.value = draft
            result = self.run_candidate()
            self.assertEqual(result['reason'], 'candidate_existing_draft_or_unknown')
            self.assertFalse(result['attempted'])
            self.assertEqual(self.raw.input.pattern.value, draft)
        self.claim.assert_not_called()
        self.assertEqual(self.raw.input.pattern.writes, [])

    def test_readonly_or_unknown_capability_is_rejected(self):
        for readonly in (1, True, None, 'false'):
            self.raw.input.pattern.CurrentIsReadOnly = readonly
            self.assertEqual(self.run_candidate()['reason'], 'candidate_input_readonly_or_unknown')
        self.claim.assert_not_called()

    def test_every_prewrite_checkpoint_can_stop_without_a_write(self):
        for stopping in ('before_discovery', 'after_discovery', 'after_read', 'before_claim',
                         'before_write', 'commit_write'):
            with self.subTest(stopping=stopping):
                self.setUp()
                self.checkpoint.side_effect = lambda phase: phase != stopping
                result = self.run_candidate()
                self.assertFalse(result['attempted'])
                self.assertEqual(result['reason'], 'candidate_checkpoint_rejected')
                self.assertEqual(self.raw.input.pattern.writes, [])
                if stopping == 'before_discovery':
                    self.factory.assert_not_called()

    def test_takeover_latched_during_final_com_read_is_checked_before_setvalue(self):
        pattern = self.raw.input.pattern
        cancelled = []
        original = type(pattern).CurrentValue
        reads = 0
        def read_value(instance):
            nonlocal reads
            reads += 1
            if reads == 2:
                cancelled.append(True)
            return original.fget(instance)
        from unittest.mock import patch
        with patch.object(type(pattern), 'CurrentValue', property(read_value)):
            result = self.run_candidate(checkpoint=lambda phase: not cancelled)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['claim_confirmed'])
        self.assertFalse(result['attempted'])
        self.assertEqual(pattern.writes, [])

    def test_unconfirmed_or_throwing_claim_has_no_write_or_retry(self):
        for failure in (False, None, RuntimeError('private quota detail')):
            self.setUp()
            self.claim.side_effect = failure if isinstance(failure, Exception) else lambda: failure
            result = self.run_candidate()
            self.assertTrue(result['claim_attempted'])
            self.assertFalse(result['claim_confirmed'])
            self.assertFalse(result['attempted'])
            self.claim.assert_called_once_with()
            self.assertEqual(self.raw.input.pattern.writes, [])
            self.assertNotIn('private', result['reason'])

    def test_final_checkpoint_change_to_runtime_draft_or_mode_blocks_com_write(self):
        for change in ('runtime', 'draft', 'mode'):
            with self.subTest(change=change):
                self.setUp()
                def checkpoint(phase):
                    if phase == 'before_write':
                        if change == 'runtime':
                            self.raw.input.runtime = (42, 99)
                        elif change == 'draft':
                            self.raw.input.pattern.value = 'new user draft'
                        else:
                            self.raw.AutoSetFocus = 1
                    return True
                result = self.run_candidate(checkpoint=checkpoint)
                self.assertTrue(result['claim_confirmed'])
                self.assertFalse(result['attempted'])
                self.assertEqual(self.raw.input.pattern.writes, [])

    def test_com_setter_exception_is_unknown_and_leaves_draft_untouched(self):
        self.raw.input.pattern.write_error = True
        result = self.run_candidate()
        self.assertEqual(result['status'], 'unknown')
        self.assertTrue(result['attempted'])
        self.assertEqual(result['reason'], 'candidate_operation_failed')
        self.assertEqual(self.raw.input.pattern.writes, [candidate.FIXED_DRAFT])
        self.assertEqual(self.raw.input.pattern.value, candidate.FIXED_DRAFT)
        self.claim.assert_called_once_with()

    def test_human_takeover_after_write_prevents_readback_and_cleanup(self):
        self.checkpoint.side_effect = lambda phase: phase != 'after_write'
        result = self.run_candidate()
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(result['readback_equal'])
        self.assertEqual(self.raw.input.pattern.writes, [candidate.FIXED_DRAFT])

    def test_readback_mismatch_is_unknown_and_never_retries_or_cleans(self):
        self.raw.input.pattern.mismatched_readback = True
        result = self.run_candidate()
        self.assertEqual(result['status'], 'unknown')
        self.assertFalse(result['readback_equal'])
        self.assertEqual(result['reason'], 'candidate_readback_mismatch')
        self.assertEqual(self.raw.input.pattern.writes, [candidate.FIXED_DRAFT])

    def test_identity_change_after_exact_readback_does_not_confirm(self):
        def checkpoint(phase):
            if phase == 'after_readback':
                self.raw.root.runtime = (42, 99)
            return True
        result = self.run_candidate(checkpoint=checkpoint)
        self.assertEqual(result['status'], 'unknown')
        self.assertTrue(result['readback_equal'])
        self.assertEqual(self.raw.input.pattern.writes, [candidate.FIXED_DRAFT])

    def test_missing_callbacks_or_invalid_binding_fails_before_client(self):
        self.assertEqual(self.run_candidate(checkpoint=None)['reason'], 'candidate_callbacks_required')
        self.target = candidate.DraftTarget(123, 321, (42, 1), (42, 1))
        self.assertEqual(self.run_candidate()['reason'], 'candidate_target_invalid')
        self.factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
