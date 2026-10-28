"""No native UIA, windows, external processes or paid API calls."""
import contextlib
import copy
import io
import json
import sys
import unittest
from unittest.mock import patch

_path = sys.path[:]
try:
    from scripts import probe_uia_invoke as p
finally:
    sys.path[:] = _path
del _path


def fixture():
    return dict(mta_verified=True, target={'a': 101, 'b': 102}, phases=[
        dict(phase=name, clicks=1, invoke_returned=True, button_event_overflow=False,
             native_full_readback_equal=True, uia_full_readback_equal=True,
             identity_stable=True, focus_before=102, focus=101 if name == 'default' else 102,
             button_focus_events=['set'] if name == 'default' else [],
             context=dict(stable=True, matches_requested_client=True,
                          associated_auto_focus=name == 'default')) for name in p.PHASES])


class InvokeProbeTests(unittest.TestCase):
    def test_default_disabled(self):
        with patch.object(p.base, 'run', side_effect=AssertionError('native')), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(p.main([]), 0)
        self.assertEqual(json.loads(out.getvalue())['state'], 'disabled')

    def test_external_targets_not_accepted(self):
        for args in (['--pid', '1'], ['--hwnd', '1'], ['--run']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                p.main(args)

    def test_full_positive_and_negative_controls(self):
        self.assertTrue(p.qualified(fixture()))
        for index in range(2):
            for change in (dict(clicks=0), dict(clicks=2), dict(clicks=True),
                           dict(invoke_returned=False), dict(identity_stable=False),
                           dict(native_full_readback_equal=False), dict(uia_full_readback_equal=False),
                           dict(button_event_overflow=True), dict(focus_before=101),
                           dict(context={'stable': True, 'matches_requested_client': False})):
                value = copy.deepcopy(fixture()); value['phases'][index].update(change)
                with self.subTest(index=index, change=change):
                    self.assertFalse(p.qualified(value))

    def test_false_client_focus_change_is_rejected(self):
        value = fixture(); value['phases'][1].update(focus=101, button_focus_events=['set'])
        self.assertFalse(p.qualified(value))

    def test_missing_positive_focus_control_is_rejected(self):
        value = fixture(); value['phases'][0].update(focus=102, button_focus_events=[])
        self.assertFalse(p.qualified(value))

    def test_phase_order_and_mta_are_required(self):
        value = fixture(); value['phases'].reverse()
        self.assertFalse(p.qualified(value))
        value = fixture(); value['mta_verified'] = False
        self.assertFalse(p.qualified(value))

    def test_execution_does_not_imply_focus_isolation(self):
        value = fixture()
        value['phases'][0]['button_focus_events'] = ['set', 'kill', 'set']
        self.assertTrue(p.qualified(value))
        value['phases'][1].update(focus=101, button_focus_events=['set'])
        self.assertFalse(p.qualified(value))
        self.assertEqual(p.observations(value), dict(invocation_observed=True,
                                                  false_client_focus_unchanged=False))

    def test_incomplete_invocation_is_not_reported_successful(self):
        self.assertEqual(p.observations({}), dict(invocation_observed=False,
                                                 false_client_focus_unchanged=None))
        value = fixture(); value['phases'][1]['clicks'] = 0
        self.assertFalse(p.observations(value)['invocation_observed'])

    def test_receiver_chain_requires_order_nesting_and_same_thread(self):
        rows = [dict(button_message=m, notification=n, active_button_messages=a,
                     thread_id=17, stage='invoke') for m, n, a in
                ((245, None, [245]), (513, None, [245, 513]),
                 (7, None, [245, 513, 7]), (None, 6, [245, 513, 7]))]
        value = dict(stack_error=False, notification_stacks=rows)
        self.assertTrue(p.receiver_relation(value))
        for index, change in ((1, dict(thread_id=18)), (2, dict(stage='prepare')),
                              (3, dict(active_button_messages=[7]))):
            bad = copy.deepcopy(value); bad['notification_stacks'][index].update(change)
            self.assertFalse(p.receiver_relation(bad))
        bad = copy.deepcopy(value); bad['notification_stacks'].reverse()
        self.assertFalse(p.receiver_relation(bad))

    def test_receiver_chain_cannot_be_inferred_from_time_or_one_notification(self):
        self.assertFalse(p.receiver_relation(dict(stack_error=False, notification_stacks=[
            dict(notification=6, active_button_messages=[7], thread_id=17, stage='invoke')])))
        self.assertFalse(p.receiver_relation(dict(stack_error=True, notification_stacks=[])))
