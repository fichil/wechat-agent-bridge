"""Synthetic COM calls and scalar snapshots only; never query a real window."""
import copy
import ctypes as c
import threading
import unittest
import uuid

from wechat_agent_bridge.adapters import virtual_desktop as vd
from wechat_agent_bridge.console_models import ConsoleError


TARGET = {'hwnd': 101, 'desktop_id': '11111111-2222-3333-4444-555555555555', 'current': False}
FOREGROUND = {'hwnd': 202, 'desktop_id': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'current': True}


class FakeManager:
    def __init__(self):
        self.calls = []
        self.value = {k: TARGET[k] for k in ('desktop_id', 'current')}
    def query(self, hwnd):
        self.calls.append(('query', hwnd))
        return self.value
    def close(self):
        self.calls.append(('close',))
        return True


class ReaderTests(unittest.TestCase):
    def test_constructor_does_not_read_windows_and_close_is_once(self):
        manager = FakeManager()
        reader = vd.VirtualDesktopReader(lambda: manager)
        self.assertEqual(manager.calls, [])
        self.assertEqual(reader.query(101), manager.value)
        self.assertTrue(reader.close())
        self.assertTrue(reader.close())
        self.assertEqual(manager.calls, [('query', 101), ('close',)])
        with self.assertRaisesRegex(ConsoleError, '^virtual_desktop_closed$'):
            reader.query(101)

    def test_invalid_handle_is_rejected_before_backend(self):
        manager = FakeManager()
        reader = vd.VirtualDesktopReader(lambda: manager)
        for hwnd in (0, -1, True, None, '101', 2**64):
            with self.subTest(hwnd=hwnd), self.assertRaises(ConsoleError):
                reader.query(hwnd)
        self.assertEqual(manager.calls, [])
        reader.close()

    def test_bad_query_results_fail_closed(self):
        manager = FakeManager()
        reader = vd.VirtualDesktopReader(lambda: manager)
        for value in (None, {}, {'desktop_id': TARGET['desktop_id'], 'current': 0},
                      {'desktop_id': str(uuid.UUID(int=0)), 'current': False},
                      {'desktop_id': 'secret-not-a-guid', 'current': False}):
            manager.value = value
            with self.assertRaisesRegex(ConsoleError, '^virtual_desktop_query_failed$'):
                reader.query(101)
        reader.close()

    def test_cross_thread_query_and_close_rejected_before_com(self):
        manager = FakeManager()
        reader = vd.VirtualDesktopReader(lambda: manager)
        failures = []
        def other_thread():
            for call in (lambda: reader.query(101), reader.close):
                try:
                    call()
                except ConsoleError as exc:
                    failures.append(str(exc))
        worker = threading.Thread(target=other_thread)
        worker.start(); worker.join()
        self.assertEqual(failures, ['virtual_desktop_wrong_thread'] * 2)
        self.assertEqual(manager.calls, [])
        reader.close()

    def test_backend_exception_does_not_expose_details(self):
        def fail():
            raise RuntimeError('sensitive-contents')
        with self.assertRaisesRegex(ConsoleError, '^virtual_desktop_unavailable$'):
            vd.VirtualDesktopReader(fail)
        manager = FakeManager()
        manager.close = fail
        reader = vd.VirtualDesktopReader(lambda: manager)
        with self.assertRaisesRegex(ConsoleError, '^virtual_desktop_close_failed$'):
            reader.close()
        with self.assertRaisesRegex(ConsoleError, '^virtual_desktop_close_failed$'):
            reader.close()


class SnapshotTests(unittest.TestCase):
    def test_current_mode_needs_current_target_and_copies_exact_identity(self):
        target={**TARGET,'current':True}
        binding=vd.validate_current_target(target)
        self.assertEqual(binding['mode'],'current_desktop_background')
        self.assertNotIn('foreground',binding)
        self.assertTrue(vd.unchanged_current_target(binding,target))
        for change in ({'hwnd':303},{'current':False},{'current':1},
                       {'desktop_id':FOREGROUND['desktop_id']}):
            with self.subTest(change=change),self.assertRaises(ConsoleError):
                vd.unchanged_current_target(binding,{**target,**change})
        target['hwnd']=999
        self.assertEqual(binding['target']['hwnd'],101)
        with self.assertRaises(ConsoleError): vd.validate_current_target(TARGET)

    def test_current_mode_does_not_relax_foreground_or_clipboard_guards(self):
        from wechat_agent_bridge.adapters.background import DesktopSnapshot,unchanged
        from dataclasses import replace
        target={**TARGET,'current':True}
        vd.validate_current_target(target)
        with self.assertRaises(ConsoleError): vd.validate_initial(target,FOREGROUND)
        baseline=DesktopSnapshot(202,203,5)
        for change in ({'foreground':101},{'focus':101},{'clipboard_sequence':6},
                       {'target_active':True},{'locked':True}):
            with self.subTest(change=change),self.assertRaises(ConsoleError):
                unchanged(baseline,replace(baseline,**change))

    def test_valid_binding_is_copied_and_unchanged(self):
        target, foreground = copy.deepcopy(TARGET), copy.deepcopy(FOREGROUND)
        initial = vd.validate_initial(target, foreground)
        self.assertTrue(vd.unchanged_snapshot(initial, target, foreground))
        target['hwnd'] = 999
        self.assertEqual(initial['target']['hwnd'], 101)

    def test_initial_requires_distinct_nonempty_desktops_and_current_foreground(self):
        for change, who in (({'current': True}, 'target'), ({'current': False}, 'foreground'),
                            ({'desktop_id': FOREGROUND['desktop_id']}, 'target'),
                            ({'desktop_id': str(uuid.UUID(int=0))}, 'target'),
                            ({'desktop_id': ''}, 'foreground'), ({'hwnd': 101}, 'foreground')):
            values = {'target': copy.deepcopy(TARGET), 'foreground': copy.deepcopy(FOREGROUND)}
            values[who].update(change)
            with self.subTest(change=change, who=who), self.assertRaises(ConsoleError):
                vd.validate_initial(**values)

    def test_changes_to_any_bound_window_desktop_or_current_state_fail(self):
        initial = vd.validate_initial(TARGET, FOREGROUND)
        for change, who in (({'hwnd': 303}, 'target'), ({'hwnd': 303}, 'foreground'),
                            ({'current': True}, 'target'), ({'current': False}, 'foreground'),
                            ({'desktop_id': '99999999-2222-3333-4444-555555555555'}, 'target'),
                            ({'desktop_id': '99999999-bbbb-cccc-dddd-eeeeeeeeeeee'}, 'foreground')):
            values = {'target': copy.deepcopy(TARGET), 'foreground': copy.deepcopy(FOREGROUND)}
            values[who].update(change)
            with self.subTest(change=change, who=who), self.assertRaises(ConsoleError):
                vd.unchanged_snapshot(initial, **values)

    def test_invalid_snapshots_and_binding_fail_with_console_error(self):
        for invalid in (None, [], {}, {'hwnd': 101, 'current': 0, 'desktop_id': TARGET['desktop_id']}):
            with self.assertRaises(ConsoleError):
                vd.validate_initial(invalid, FOREGROUND)
            with self.assertRaises(ConsoleError):
                vd.unchanged_snapshot(invalid, TARGET, FOREGROUND)


class NativeContractTests(unittest.TestCase):
    def make(self, *, init=0, create=0, current=0, current_hr=0, id_hr=0, release_fail=False):
        calls = []
        class Api:
            def CoInitializeEx(self, reserved, kind):
                calls.append(('init', reserved, kind, threading.get_ident()))
                return init
            def CoCreateInstance(self, clsid, outer, context, iid, pointer):
                calls.append(('create', bytes(c.cast(clsid, c.POINTER(vd.GUID)).contents), outer, context,
                              bytes(c.cast(iid, c.POINTER(vd.GUID)).contents)))
                if not create:
                    c.cast(pointer, c.POINTER(c.c_void_p))[0] = c.c_void_p(1)
                return create
            def CoUninitialize(self):
                calls.append(('uninit', threading.get_ident()))
        def binder(_pointer, slot, _result, *_arguments):
            calls.append(('bind', slot))
            def method(_self, *args):
                calls.append(('call', slot, threading.get_ident()))
                if slot == 2:
                    if release_fail:
                        raise RuntimeError('release-failure')
                    return 0
                if slot == 3:
                    c.cast(args[1], c.POINTER(vd.BOOL))[0] = current
                    return current_hr
                if slot == 4:
                    data = uuid.UUID(TARGET['desktop_id']).bytes_le
                    c.memmove(args[1], data, 16)
                    return id_hr
                raise AssertionError('write_slot_bound')
            return method
        return calls, lambda: vd._NativeDesktopManager(Api(), binder)

    def test_exact_public_interface_read_slots_and_balanced_apartment(self):
        calls, factory = self.make(init=1)
        reader = vd.VirtualDesktopReader(factory)
        self.assertEqual([x for x in calls if x[0] == 'call'], [])
        self.assertEqual([x[1] for x in calls if x[0] == 'bind'], [2, 3, 4])
        created = [x for x in calls if x[0] == 'create'][0]
        self.assertEqual(created[1], vd.CLSID.bytes_le)
        self.assertEqual(created[4], vd.IID.bytes_le)
        self.assertEqual(reader.query(101), {'desktop_id': TARGET['desktop_id'], 'current': False})
        reader.close(); reader.close()
        self.assertEqual([x[1] for x in calls if x[0] == 'call'], [3, 4, 2])
        self.assertEqual(len([x for x in calls if x[0] == 'uninit']), 1)
        self.assertEqual([x[-1] for x in calls if x[0] in ('init', 'call', 'uninit')],
                         [threading.get_ident()] * 5)

    def test_failed_init_does_not_uninitialize_unowned_apartment(self):
        calls, factory = self.make(init=-2147417850)
        with self.assertRaises(ConsoleError):
            vd.VirtualDesktopReader(factory)
        self.assertEqual([x[0] for x in calls], ['init'])

    def test_failed_create_balances_successful_initialization(self):
        calls, factory = self.make(create=-1)
        with self.assertRaises(ConsoleError):
            vd.VirtualDesktopReader(factory)
        self.assertEqual([x[0] for x in calls], ['init', 'create', 'uninit'])

    def test_query_hresult_and_invalid_bool_rejected(self):
        for arguments in ({'current_hr': -1}, {'id_hr': -1}, {'current': -1}):
            calls, factory = self.make(**arguments)
            reader = vd.VirtualDesktopReader(factory)
            with self.assertRaisesRegex(ConsoleError, '^virtual_desktop_query_failed$'):
                reader.query(101)
            reader.close()
            self.assertEqual(len([x for x in calls if x[0] == 'uninit']), 1)

    def test_release_failure_still_uninitializes_once_without_retry(self):
        calls, factory = self.make(release_fail=True)
        reader = vd.VirtualDesktopReader(factory)
        with self.assertRaisesRegex(ConsoleError, '^virtual_desktop_close_failed$'):
            reader.close()
        with self.assertRaisesRegex(ConsoleError, '^virtual_desktop_close_failed$'):
            reader.close()
        self.assertEqual([x[1] for x in calls if x[0] == 'call'], [2])
        self.assertEqual(len([x for x in calls if x[0] == 'uninit']), 1)


if __name__ == '__main__':
    unittest.main()
