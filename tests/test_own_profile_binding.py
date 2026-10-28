"""Own-profile binding through direct HWNDs, with no live UI or chat reads."""
import ctypes
from ctypes import wintypes
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
from wechat_agent_bridge.console_models import ConsoleError


PID, HWND, PROFILE = 77, 101, 202
WECHAT_ID = 'wxid_synthetic_account'


class Info:
    def __init__(self, pid, classname):
        self.process_id, self._class, self.class_reads = pid, classname, 0
    @property
    def class_name(self):
        self.class_reads += 1
        return self._class


class Window:
    def __init__(self, handle, classname, *, pid=PID, text=None, children=()):
        self.handle, self.element_info = handle, Info(pid, classname)
        self.text, self._children, self.text_reads = text, list(children), 0
    def window_text(self):
        self.text_reads += 1
        if self.text is None:
            raise AssertionError('unrelated_window_text_requested')
        return self.text
    def children(self):
        if self.text is None:
            raise AssertionError('unrelated_window_children_requested')
        return self._children


class DirectDesktop:
    def __init__(self, windows):
        self.by_handle, self.requests = windows, []
    def windows(self, **_kwargs):
        raise AssertionError('global_uia_enumeration_forbidden')
    def window(self, *, handle):
        self.requests.append(handle)
        return SimpleNamespace(wrapper_object=lambda: self.by_handle[handle])


class OwnProfileTests(unittest.TestCase):
    def setup_binding(self, *, root=None, profiles=None, extra=None):
        root = root or Window(HWND, 'mmui::MainWindow')
        profiles = [Window(PROFILE, 'mmui::ProfileUniquePop', text='微信号：' + WECHAT_ID)] if profiles is None else profiles
        values = [root, *profiles, *(extra or [])]
        desktop = DirectDesktop({item.handle: item for item in values})
        adapter = WindowsUIA()
        def bind(pid, hwnd):
            self.assertEqual((pid, hwnd), (PID, HWND))
            key = f'{pid}:{hwnd}'
            adapter.instances = {key: (root, '4.1.15.56')}
            return key
        adapter.bind_window = bind
        adapter._visible_process_window_handles = lambda pid: tuple(item.handle for item in values)
        patched = patch.dict(sys.modules, {'pywinauto': SimpleNamespace(Desktop=lambda **kwargs: desktop)})
        patched.start()
        self.addCleanup(patched.stop)
        return adapter, root, profiles, desktop

    def test_off_current_desktop_direct_binding_preserves_exact_identity_source(self):
        adapter, root, profiles, desktop = self.setup_binding()
        result = adapter.verify_own_profile(PID, HWND, WECHAT_ID)
        self.assertEqual(result['wechat_id'], WECHAT_ID)
        self.assertEqual(result['instance_id'], f'{PID}:{HWND}')
        self.assertEqual(result['version'], '4.1.15.56')
        self.assertEqual(result['identity_source'], 'uia_own_profile_exact_id_user_selected_window')
        self.assertGreater(adapter.profile_verified_at, 0)
        self.assertEqual(root.text_reads, 0)
        self.assertEqual(profiles[0].text_reads, 1)
        self.assertEqual(desktop.requests, [PROFILE])

    def test_read_only_enrollment_does_not_publish_account_before_commit(self):
        adapter,root,profiles,_=self.setup_binding()
        result=adapter.read_own_profile(PID,HWND,WECHAT_ID)
        self.assertEqual(result['wechat_id'],WECHAT_ID)
        self.assertFalse(adapter.accounts)
        self.assertEqual(root.text_reads,0)
        self.assertEqual(profiles[0].text_reads,1)

    def test_other_same_process_classes_never_read_text(self):
        extra = [Window(404, 'mmui::ChatWindow'), Window(505, 'mmui::ContactProfile')]
        adapter, root, profiles, desktop = self.setup_binding(extra=extra)
        adapter.verify_own_profile(PID, HWND, WECHAT_ID)
        self.assertEqual([item.text_reads for item in [root, *extra]], [0] * 3)
        self.assertEqual(desktop.requests, [PROFILE, 404, 505])

    def test_second_same_process_main_rejects_before_profile_fields(self):
        other_main = Window(303, 'mmui::MainWindow')
        adapter, root, profiles, _desktop = self.setup_binding(extra=[other_main])
        with self.assertRaisesRegex(ConsoleError, '^account_window_identity_ambiguous$'):
            adapter.verify_own_profile(PID, HWND, WECHAT_ID)
        self.assertEqual([w.text_reads for w in [root, other_main, *profiles]], [0, 0, 0])
        self.assertFalse(adapter.accounts)

    def test_bound_root_absent_from_native_visible_list_rejects(self):
        adapter, root, profiles, desktop = self.setup_binding()
        adapter._visible_process_window_handles = lambda pid: (PROFILE,)
        with self.assertRaisesRegex(ConsoleError, '^account_window_identity_ambiguous$'):
            adapter.verify_own_profile(PID, HWND, WECHAT_ID)
        self.assertEqual(root.text_reads, 0)
        self.assertEqual(profiles[0].text_reads, 0)
        self.assertEqual(desktop.requests, [])

    def test_missing_profile_is_waitable_not_main_window_ambiguity(self):
        adapter, root, _profiles, _desktop = self.setup_binding(profiles=[])
        with self.assertRaisesRegex(ConsoleError, '^account_identity_uncertain_open_own_profile$'):
            adapter.verify_own_profile(PID, HWND, WECHAT_ID)
        self.assertEqual(root.text_reads, 0)
        self.assertFalse(adapter.accounts)

    def test_two_profiles_are_ambiguous_before_any_field_read(self):
        profiles = [Window(202, 'mmui::ProfileUniquePop', text='微信号：' + WECHAT_ID),
                    Window(303, 'mmui::ProfileUniquePop', text='微信号：another_account')]
        adapter, _root, _profiles, _desktop = self.setup_binding(profiles=profiles)
        with self.assertRaisesRegex(ConsoleError, '^account_profile_identity_ambiguous$'):
            adapter.verify_own_profile(PID, HWND, WECHAT_ID)
        self.assertEqual([p.text_reads for p in profiles], [0, 0])

    def test_bound_main_class_pid_and_hwnd_are_required(self):
        for root in (Window(HWND, 'mmui::ChatWindow'), Window(HWND, 'mmui::MainWindow', pid=78),
                     Window(999, 'mmui::MainWindow')):
            with self.subTest(handle=root.handle, pid=root.element_info.process_id):
                adapter, _, profiles, _desktop = self.setup_binding(root=root)
                with self.assertRaisesRegex(ConsoleError, '^account_window_identity_ambiguous$'):
                    adapter.verify_own_profile(PID, HWND, WECHAT_ID)
                self.assertEqual(profiles[0].text_reads, 0)
                self.assertEqual(root.text_reads, 0)

    def test_reused_candidate_hwnd_with_foreign_pid_is_not_inspected(self):
        foreign = Window(PROFILE, 'mmui::ProfileUniquePop', pid=78, text='private')
        adapter, _root, _profiles, _desktop = self.setup_binding(profiles=[foreign])
        with self.assertRaisesRegex(ConsoleError, '^account_window_identity_ambiguous$'):
            adapter.verify_own_profile(PID, HWND, WECHAT_ID)
        self.assertEqual(foreign.element_info.class_reads, 0)
        self.assertEqual(foreign.text_reads, 0)

    def test_candidate_wrapper_handle_mismatch_rejected(self):
        adapter, _root, profiles, desktop = self.setup_binding()
        desktop.by_handle[PROFILE] = Window(999, 'mmui::ProfileUniquePop', text='private')
        with self.assertRaisesRegex(ConsoleError, '^account_window_identity_ambiguous$'):
            adapter.verify_own_profile(PID, HWND, WECHAT_ID)
        self.assertEqual(profiles[0].text_reads, 0)

    def test_unreadable_candidate_cannot_be_ignored_to_claim_uniqueness(self):
        adapter, _root, profiles, desktop = self.setup_binding(extra=[Window(303, 'other')])
        del desktop.by_handle[303]
        with self.assertRaisesRegex(ConsoleError, '^account_window_identity_ambiguous$'):
            adapter.verify_own_profile(PID, HWND, WECHAT_ID)
        self.assertEqual(profiles[0].text_reads, 0)

    def test_only_labeled_exact_id_verifies_never_nickname(self):
        for text in (WECHAT_ID, '昵称：' + WECHAT_ID, '微信号：different_account',
                     '微信号：' + WECHAT_ID + '\n微信号：another_account'):
            adapter, _root, _profiles, _desktop = self.setup_binding(
                profiles=[Window(PROFILE, 'mmui::ProfileUniquePop', text=text)])
            with self.subTest(text=text), self.assertRaisesRegex(ConsoleError, '^account_profile_id_mismatch$'):
                adapter.verify_own_profile(PID, HWND, WECHAT_ID)
            self.assertFalse(adapter.accounts)

    def test_overwide_profile_cannot_hide_conflicting_id_after_thirtieth_child(self):
        children = [Window(1000+i,'synthetic',text='') for i in range(30)]
        conflict = Window(2000,'synthetic',text='微信号：another_account')
        profile = Window(PROFILE,'mmui::ProfileUniquePop',text='微信号：'+WECHAT_ID,
                         children=[*children,conflict])
        adapter,root,_,_ = self.setup_binding(profiles=[profile])
        with self.assertRaisesRegex(ConsoleError,'^account_profile_tree_incomplete$'):
            adapter.verify_own_profile(PID,HWND,WECHAT_ID)
        self.assertFalse(adapter.accounts)
        self.assertEqual(root.text_reads,0)
        self.assertTrue(all(child.text_reads==0 for child in [*children,conflict]))

    def test_exact_child_limit_reads_complete_profile_and_rejects_conflict(self):
        children = [Window(1000+i,'synthetic',text='') for i in range(29)]
        conflict = Window(2000,'synthetic',text='微信号：another_account')
        profile = Window(PROFILE,'mmui::ProfileUniquePop',text='微信号：'+WECHAT_ID,
                         children=[*children,conflict])
        adapter,_,_,_ = self.setup_binding(profiles=[profile])
        with self.assertRaisesRegex(ConsoleError,'^account_profile_id_mismatch$'):
            adapter.verify_own_profile(PID,HWND,WECHAT_ID)
        self.assertEqual(conflict.text_reads,1)
        self.assertFalse(adapter.accounts)

    def test_total_node_limit_never_accepts_partial_profile(self):
        last = Window(3000,'synthetic',text='微信号：another_account')
        chain = last
        for i in range(400):
            chain=Window(1000+i,'synthetic',text='',children=[chain])
        profile=Window(PROFILE,'mmui::ProfileUniquePop',text='微信号：'+WECHAT_ID,children=[chain])
        adapter,_,_,_=self.setup_binding(profiles=[profile])
        with self.assertRaisesRegex(ConsoleError,'^account_profile_tree_incomplete$'):
            adapter.verify_own_profile(PID,HWND,WECHAT_ID)
        self.assertEqual(last.text_reads,0)
        self.assertFalse(adapter.accounts)


class NativeEnumerationTests(unittest.TestCase):
    def fake_user(self, rows, *, enumerate_ok=True):
        visible_calls = []
        class Function:
            def __init__(self, function):
                self.function = function
            def __call__(self, *args):
                return self.function(*args)
        by_handle = {row[0]: row for row in rows}
        def owner(hwnd, output):
            ctypes.cast(output, ctypes.POINTER(wintypes.DWORD)).contents.value = by_handle[hwnd][1]
            return 99
        def visible(hwnd):
            visible_calls.append(hwnd)
            return by_handle[hwnd][2]
        def enumerate_windows(callback, value):
            if not enumerate_ok:
                return 0
            for hwnd, _pid, _visible in rows:
                if not callback(hwnd, value):
                    return 0
            return 1
        return SimpleNamespace(GetWindowThreadProcessId=Function(owner), IsWindowVisible=Function(visible),
                               EnumWindows=Function(enumerate_windows)), visible_calls

    def test_native_enumeration_filters_pid_then_visibility_and_deduplicates(self):
        user, visible = self.fake_user([(101, PID, True), (202, 88, True), (303, PID, False),
                                       (404, PID, True), (404, PID, True)])
        with patch('wechat_agent_bridge.adapters.windows_uia.ctypes.WinDLL', return_value=user, create=True):
            self.assertEqual(WindowsUIA._visible_process_window_handles(PID), (101, 404))
        self.assertNotIn(202, visible)

    def test_failed_native_enumeration_is_not_missing_profile(self):
        user, _ = self.fake_user([], enumerate_ok=False)
        with (patch('wechat_agent_bridge.adapters.windows_uia.ctypes.WinDLL', return_value=user, create=True),
              self.assertRaisesRegex(ConsoleError, '^account_window_enumeration_failed$')):
            WindowsUIA._visible_process_window_handles(PID)

    def test_window_count_bound_fails_without_partial_result(self):
        user, _ = self.fake_user([(100 + index, PID, True) for index in range(65)])
        with (patch('wechat_agent_bridge.adapters.windows_uia.ctypes.WinDLL', return_value=user, create=True),
              self.assertRaisesRegex(ConsoleError, '^account_window_enumeration_failed$')):
            WindowsUIA._visible_process_window_handles(PID)


if __name__ == '__main__':
    unittest.main()
