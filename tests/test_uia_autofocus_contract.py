"""Synthetic state contracts only; no COM, windows, ETW or client connection."""
import copy
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import probe_uia_autofocus as probe
from wechat_agent_bridge.console_models import ConsoleError


class Fixture:
    def __init__(self):
        self.target = {'pid': 31, 'a': 101, 'b': 102}
        self.uia = SimpleNamespace(CUIAutomation='legacy', CUIAutomation8='modern',
            IUIAutomation='base', IUIAutomation2='v2', UIA_ValuePatternId=10002,
            IUIAutomationValuePattern='value')
        self.clients, self.calls, self.events = [], [], []
        self.focus, self.value = self.target['b'], ''
        self.change_policy_before_write = False
        self.identity_override = None
        self.existing_draft = ''
        self.write_error = False
        self.ignore_false = False
        self.runtime_changed = False

    def create(self, kind, interface):
        fixture = self

        class Client:
            def __init__(self):
                self.flag = 1
                self.reads = 0

            @property
            def AutoSetFocus(self):
                return self.flag

            @AutoSetFocus.setter
            def AutoSetFocus(self, value):
                if not fixture.ignore_false:
                    self.flag = value

            def ElementFromHandle(self, hwnd):
                fixture.calls.append(('element', kind, interface, hwnd, self.flag))
                fixture.value = fixture.existing_draft
                client = self

                class Element:
                    CurrentProcessId = fixture.identity_override or fixture.target['pid']
                    CurrentNativeWindowHandle = fixture.target['a']

                    def GetRuntimeId(self):
                        client.reads += 1
                        return [42, 999 if fixture.runtime_changed and client.reads > 1 else 101]

                    def GetCurrentPattern(self, pattern_id):
                        assert pattern_id == 10002

                        class Pattern:
                            CurrentIsReadOnly = False

                            def QueryInterface(self, interface):
                                assert interface == 'value'
                                return self

                            @property
                            def CurrentValue(self):
                                return fixture.value

                            def SetValue(self, text):
                                fixture.calls.append(('write', kind, client.flag))
                                if fixture.write_error:
                                    raise RuntimeError('synthetic_uncertain_write')
                                fixture.value = text
                                if client.flag:
                                    fixture.focus = fixture.target['a']
                                    fixture.events = [[fixture.target['b'], 'kill'], [fixture.target['a'], 'set']]

                        return Pattern()

                return Element()

        client = Client()
        self.clients.append(client)
        return client

    def exchange(self, action, name):
        if action == 'prepare':
            self.focus, self.events = self.target['b'], []
            return {'focus': self.focus}
        if action == 'check':
            if self.change_policy_before_write:
                self.clients[-1].flag = 1
            return {'focus': self.focus, 'focus_events': self.events}
        if action == 'complete':
            return {'focus': self.focus, 'focus_events': self.events,
                    'native_full_readback_equal': self.value == probe.TEXT[name], 'event_overflow': False}
        raise AssertionError(action)

    def execute(self, name, evidence=None):
        return probe.phase(self.create, self.uia, self.target, self.exchange, name, evidence)

    def result(self):
        return {'target': self.target, 'mta_verified': True,
                'phases': [self.execute(name) for name in probe.PHASES]}


class AutoFocusContractTests(unittest.TestCase):
    def test_each_phase_uses_its_fresh_client_and_configured_element(self):
        fixture = Fixture()
        result = fixture.result()
        self.assertTrue(probe.source_qualified(result))
        self.assertEqual(len({id(client) for client in fixture.clients}), 3)
        self.assertEqual(fixture.calls, [
            ('element', 'legacy', 'base', 101, 1), ('write', 'legacy', 1),
            ('element', 'modern', 'v2', 101, 1), ('write', 'modern', 1),
            ('element', 'modern', 'v2', 101, 0), ('write', 'modern', 0)])

    def test_ignored_false_setting_never_obtains_an_element(self):
        fixture = Fixture()
        fixture.ignore_false = True
        with self.assertRaisesRegex(ConsoleError, 'uia_auto_focus_not_disabled'):
            fixture.execute('modern_no_focus')
        self.assertEqual(fixture.calls, [])

    def test_policy_change_between_discovery_and_action_stops_write(self):
        fixture = Fixture()
        fixture.change_policy_before_write = True
        with self.assertRaisesRegex(ConsoleError, 'uia_auto_focus_not_disabled'):
            fixture.execute('modern_no_focus')
        self.assertEqual(fixture.calls, [('element', 'modern', 'v2', 101, 0)])

    def test_foreign_identity_draft_and_runtime_replacement_never_write(self):
        for field, value in (('identity_override', 99), ('existing_draft', 'existing'), ('runtime_changed', True)):
            with self.subTest(field=field):
                fixture = Fixture()
                setattr(fixture, field, value)
                with self.assertRaises(RuntimeError):
                    fixture.execute('modern_no_focus')
                self.assertFalse(any(call[0] == 'write' for call in fixture.calls))

    def test_uncertain_write_is_claimed_once_and_not_retried(self):
        fixture, evidence = Fixture(), []
        fixture.write_error = True
        with self.assertRaisesRegex(RuntimeError, 'synthetic_uncertain_write'):
            fixture.execute('modern_no_focus', evidence)
        self.assertEqual(sum(call[0] == 'write' for call in fixture.calls), 1)
        self.assertTrue(evidence[0]['attempted'])
        self.assertNotIn('uia_full_readback_equal', evidence[0])

    def test_no_focus_result_needs_both_default_positive_controls(self):
        result = Fixture().result()
        for index in (0, 1):
            changed = copy.deepcopy(result)
            changed['phases'][index].update(focus=102, focus_events=[])
            self.assertFalse(probe.source_qualified(changed))

    def test_transient_focus_or_inexact_readback_cannot_pass(self):
        result = Fixture().result()
        for change in ({'focus_events': [[101, 'set'], [102, 'set']]},
                       {'native_full_readback_equal': False}, {'uia_full_readback_equal': False},
                       {'identity_stable': False}, {'event_overflow': True},
                       {'default_auto_set_focus': 0}, {'configured_auto_set_focus': 1}):
            changed = copy.deepcopy(result)
            changed['phases'][2].update(change)
            self.assertFalse(probe.source_qualified(changed), change)

    def test_result_order_and_mta_are_part_of_source_qualification(self):
        result = Fixture().result()
        for change in ({'mta_verified': False}, {'error': 'timeout'}, {'phases': result['phases'][::-1]}):
            self.assertFalse(probe.source_qualified(dict(result, **change)))

    def test_success_needs_all_cleanup_and_unchanged_observations(self):
        cleanup = {key: True for key in ('child_exited', 'native_cleanup_confirmed',
            'owned_windows_absent', 'desktop_closed', 'desktop_absent')}
        report = {'result': Fixture().result(), 'source_unchanged': True, 'uia_dll_unchanged': True,
                  'input_checkpoints_unchanged': True, 'checkpoint_count': 20, 'cleanup': cleanup}
        self.assertTrue(probe.complete_pass(report))
        for key in cleanup:
            self.assertFalse(probe.complete_pass(dict(report, cleanup=dict(cleanup, **{key: False}))))
        for change in ({'terminated': True}, {'errors': ['unconfirmed']}):
            self.assertFalse(probe.complete_pass(dict(report, cleanup=dict(cleanup, **change))))
        for change in ({'source_unchanged': False}, {'uia_dll_unchanged': False}, {'input_checkpoints_unchanged': False},
                       {'checkpoint_count': 1}, {'error': 'timeout'}):
            self.assertFalse(probe.complete_pass(dict(report, **change)))

    def test_import_com_is_unregistered_then_balanced_once_on_cleanup(self):
        operations = []
        module = SimpleNamespace(__version__='1.4.17', _shutdown=lambda: operations.append('shutdown'))
        with patch.object(probe.gc, 'collect', side_effect=lambda: operations.append('release')), \
                patch.object(probe.atexit, 'unregister', side_effect=lambda fn: operations.append(('unregister', fn))):
            self.assertTrue(probe.close_import_com(module))
        self.assertEqual(operations, ['release', ('unregister', module._shutdown), 'shutdown'])

    def test_import_com_cleanup_refuses_different_library_version(self):
        module = SimpleNamespace(__version__='different', _shutdown=Mock())
        with patch.object(probe.atexit, 'unregister') as unregister:
            with self.assertRaisesRegex(RuntimeError, 'comtypes_cleanup_version_mismatch'):
                probe.close_import_com(module)
        module._shutdown.assert_not_called()
        unregister.assert_not_called()

    def test_default_entry_cannot_start_native_run(self):
        with patch.object(probe, 'run', side_effect=AssertionError('must stay disabled')) as native:
            with patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(probe.main([]), 0)
            native.assert_not_called()
            self.assertIn('"state": "disabled"', output.getvalue())

    def test_cleanup_terminates_only_the_owned_child_and_still_closes_after_probe_error(self):
        child = Mock(pid=4321, exitcode=1)
        child.is_alive.side_effect = [True, False]
        closed = []

        def native(_user, name, _return_type, _arguments):
            if name == 'IsWindow':
                return Mock(side_effect=OSError('synthetic probe error'))
            if name == 'CloseDesktop':
                return lambda handle: closed.append(handle) or 1
            if name == 'OpenDesktopW':
                return lambda *_args: 0
            raise AssertionError(name)

        with patch.object(probe, 'call', side_effect=native), patch.object(probe.c, 'set_last_error', create=True), \
                patch.object(probe.c, 'get_last_error', return_value=2, create=True):
            result = probe.cleanup_owned(child, object(), 88, 'own-name', {'host': 1, 'a': 2, 'b': 3}, {})
        child.terminate.assert_called_once_with()
        self.assertEqual(closed, [88])
        self.assertTrue(result['terminated'])
        self.assertFalse(result['child_exited'])
        self.assertFalse(result['native_cleanup_confirmed'])
        self.assertIn('owned_windows_absence_unconfirmed', result['errors'])

    def test_missing_native_cleanup_is_never_replaced_by_process_exit(self):
        child = Mock(pid=4321, exitcode=0)
        child.is_alive.return_value = False
        def native(_user, name, _return_type, _arguments):
            return lambda *_args: 1 if name == 'CloseDesktop' else 0
        with patch.object(probe, 'call', side_effect=native), patch.object(probe.c, 'set_last_error', create=True), \
                patch.object(probe.c, 'get_last_error', return_value=2, create=True):
            result = probe.cleanup_owned(child, object(), 88, 'own-name', {'host': 1, 'a': 2, 'b': 3}, {})
        self.assertTrue(result['child_exited'])
        self.assertTrue(result['owned_windows_absent'])
        self.assertTrue(result['desktop_absent'])
        self.assertFalse(result['native_cleanup_confirmed'])
        child.terminate.assert_not_called()

    def test_cli_has_no_pid_hwnd_desktop_or_foreground_fallback_input(self):
        for argument in ('--pid', '--hwnd', '--desktop', '--input-desktop', '--foreground'):
            with self.subTest(argument=argument), patch('sys.stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    probe.main([argument, '123'])


if __name__ == '__main__':
    unittest.main()
