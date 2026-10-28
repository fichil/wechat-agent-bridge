"""Pure launch and qualification contracts; no native calls."""
import contextlib
import copy
import io
import json
import unittest
import sys
from unittest.mock import patch

_discovery_path = sys.path[:]
try:
    from scripts import probe_uia_node_context as p
finally:
    sys.path[:] = _discovery_path
del _discovery_path


def fixture():
    rows = []
    for name in p.PHASES:
        rows.append(dict(phase=name, read_only_uia=True, setvalue_called=False,
            invoke_called=False, context_status='verified_observation', context={'stable': True},
            public_auto_focus=not name.endswith('_false'),
            lookup='tree' if name.startswith('tree') else 'direct', focus_before=102, focus=102,
            focus_events=[], identity_stable=True, empty_before_and_after=True,
            native_full_readback_equal=True))
    return dict(mta_verified=True, phases=rows, target={'b':102})


class ProbeTests(unittest.TestCase):
    def test_default_is_disabled_before_any_native_port(self):
        with patch.object(p.cross, 'run', side_effect=AssertionError('native')), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(p.main([]), 0)
        self.assertEqual(json.loads(out.getvalue())['state'], 'disabled')

    def test_external_pid_is_not_an_option(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            p.main(['--pid','1'])

    def test_actual_client_child_gets_readonly_phase_and_restores(self):
        original_phase, original_phases = p.tree.tree_phase, p.tree.PHASES
        def child():
            self.assertIs(p.tree.tree_phase, p.context_phase)
            self.assertEqual(p.tree.PHASES, p.PHASES)
            return True
        with patch.object(p, 'ORIGINAL_CLIENT', child):
            self.assertTrue(p.client_main())
        self.assertIs(p.tree.tree_phase, original_phase)
        self.assertEqual(p.tree.PHASES, original_phases)

    def test_only_owned_provider_expects_empty_text_and_restores(self):
        original = p.tree.candidate.FIXED_DRAFT
        def child():
            self.assertEqual(p.tree.candidate.FIXED_DRAFT, '')
            self.assertEqual(p.tree.PHASES, p.PHASES)
        with patch.object(p, 'ORIGINAL_PROVIDER', child):
            p.provider_main()
        self.assertEqual(p.tree.candidate.FIXED_DRAFT, original)

    def test_four_verified_readonly_observations_are_required(self):
        self.assertTrue(p.source_qualified(fixture()))
        for change in (dict(context_status='unverified'), dict(setvalue_called=True),
                       dict(invoke_called=True), dict(focus_events=[[101,'set']]),
                       dict(identity_stable=False), dict(native_full_readback_equal=False),
                       dict(context={'stable':False}), dict(public_auto_focus=False)):
            r=fixture();r['phases'][0].update(change)
            with self.subTest(change=change):
                self.assertFalse(p.source_qualified(r))

    def test_missing_or_malformed_observation_never_qualifies(self):
        for value in (None, {}, {'phases':[1]}, fixture()):
            r=copy.deepcopy(value)
            if r and 'target' in r:
                r['phases'].pop()
            self.assertFalse(p.source_qualified(r))
