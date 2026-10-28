"""Public Windows UI metadata and own-profile UI, with bounded restoration.

No process-memory reads/writes, account files, chat trees, clipboard or model API.
All native imports are lazy so synthetic tests remain platform independent.
"""
import time

from ..console_models import ConsoleError


def native_stamp(hwnd):
    import win32api
    import win32gui
    import win32process
    _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
    handle = win32api.OpenProcess(0x1000, False, pid)
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                     wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        path, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        if not kernel.QueryFullProcessImageNameW(int(handle), 0, path, ctypes.byref(length)):
            raise OSError
        if path.value.rsplit('\\', 1)[-1].lower() not in ('weixin.exe', 'wechat.exe'):
            raise OSError
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(int(handle), *(ctypes.byref(value) for value in times)):
            raise OSError
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        info = win32api.GetFileVersionInfo(path.value, '\\')
        ms, ls = info['FileVersionMS'], info['FileVersionLS']
        version = f'{ms >> 16}.{ms & 65535}.{ls >> 16}.{ls & 65535}'
        if not win32gui.IsWindow(hwnd) or win32process.GetWindowThreadProcessId(hwnd)[1] != pid:
            raise OSError
        return (pid, hwnd, created, path.value, version)
    except Exception:
        raise ConsoleError('account_discovery_instance_changed') from None
    finally:
        handle.Close()


def restore_window(receipt):
    """Also called by the parent after a timed-out/crashed UIA worker."""
    import win32api
    import win32con
    import win32gui
    import win32process
    hwnd = receipt['stamp'][1]
    if native_stamp(hwnd) != tuple(receipt['stamp']):
        raise ConsoleError('account_discovery_instance_changed')
    popup = receipt.get('popup')
    if popup and win32gui.IsWindow(popup):
        if (win32process.GetWindowThreadProcessId(popup)[1] != receipt['stamp'][0] or
                win32gui.GetWindow(popup, win32con.GW_OWNER) != hwnd):
            raise ConsoleError('account_discovery_restore_failed')
        win32gui.SendMessageTimeout(popup, win32con.WM_CLOSE, 0, 0,
                                   win32con.SMTO_ABORTIFHUNG, 500)
        if win32gui.IsWindow(popup) and win32gui.IsWindowVisible(popup):
            raise ConsoleError('account_discovery_restore_failed')
    # Do not overwrite a user's new foreground/focus if they took over.
    if receipt.get('last_input') != win32api.GetLastInputInfo():
        raise ConsoleError('account_discovery_human_takeover')
    try:
        placement = receipt['placement']
        if win32gui.GetWindowPlacement(hwnd) != placement:
            win32gui.SetWindowPlacement(hwnd, placement)
        foreground = receipt['foreground']
        if foreground and win32gui.IsWindow(foreground) and win32gui.GetForegroundWindow() != foreground:
            win32gui.SetForegroundWindow(foreground)
        cursor = receipt.get('cursor_after')
        if cursor is not None and win32api.GetCursorPos() == tuple(cursor):
            win32api.SetCursorPos(tuple(receipt['cursor_before']))
        if win32gui.GetWindowPlacement(hwnd) != placement:
            raise ValueError
    except Exception:
        raise ConsoleError('account_discovery_restore_failed') from None


class WindowsAccountMetadata:
    def __init__(self, checkpoint=lambda receipt: None):
        from pywinauto import uia_defines  # Generate/load the existing UIA typelib.
        from comtypes.gen import UIAutomationClient as uia
        from .uia_client import BackgroundUIAClient
        self.client = BackgroundUIAClient()
        self.uia = uia
        self.checkpoint = checkpoint

    def stamp(self, window):
        return native_stamp(window)

    def _root(self, hwnd, classname=None):
        import win32process
        node = self.client.element_from_handle(hwnd)
        if (node.CurrentNativeWindowHandle != hwnd or
                node.CurrentProcessId != win32process.GetWindowThreadProcessId(hwnd)[1] or
                (classname and node.CurrentClassName != classname)):
            raise ConsoleError('account_window_identity_ambiguous')
        return node

    def windows(self):
        import win32gui
        import win32process
        from .windows_uia import WindowsUIA
        processes = {pid for pid, _path, _version in WindowsUIA._processes()}
        candidates = []
        def collect(hwnd, _argument):
            if (win32process.GetWindowThreadProcessId(hwnd)[1] in processes and
                    win32gui.IsWindowVisible(hwnd)):
                candidates.append(hwnd)
        win32gui.EnumWindows(collect, None)
        if len(candidates) > 64:
            raise ConsoleError('account_window_enumeration_failed')
        result = []
        for hwnd in candidates[:64]:
            try:
                if self._root(hwnd).CurrentClassName in ('mmui::MainWindow', 'WeChatMainWndForPC'):
                    result.append(hwnd)
            except ConsoleError:
                continue
        return result

    def own_fields(self, hwnd):
        """Read labeled properties of the unique own-avatar node only. No actions."""
        from .readonly_account import labeled_fields
        root = self._root(hwnd, 'mmui::MainWindow')
        raw, uia = self.client._client, self.uia
        self.client.require_background_mode()
        navs = root.FindAll(uia.TreeScope_Descendants,
                            raw.CreatePropertyCondition(uia.UIA_ClassNamePropertyId, 'mmui::MainTabBar'))
        if navs.Length == 0:
            return {}
        if navs.Length != 1:
            raise ConsoleError('account_window_identity_ambiguous')
        nav = navs.GetElement(0)
        if nav.CurrentProcessId != root.CurrentProcessId:
            raise ConsoleError('account_window_identity_ambiguous')
        avatars = nav.FindAll(uia.TreeScope_Descendants,
                              raw.CreatePropertyCondition(uia.UIA_ClassNamePropertyId, 'mmui::XAvatarImage'))
        if avatars.Length == 0:
            return {}
        if avatars.Length != 1:
            raise ConsoleError('account_window_identity_ambiguous')
        avatar = avatars.GetElement(0)
        identity = tuple(avatar.GetRuntimeId())
        if avatar.CurrentProcessId != root.CurrentProcessId or not identity:
            raise ConsoleError('account_window_identity_ambiguous')
        values = [avatar.GetCurrentPropertyValue(prop) for prop in (30005, 30013, 30159)]
        if tuple(avatar.GetRuntimeId()) != identity or avatar.CurrentProcessId != root.CurrentProcessId:
            raise ConsoleError('account_discovery_instance_changed')
        self.client.require_background_mode()
        return labeled_fields(values)


class WindowsOwnAccountBackend(WindowsAccountMetadata):

    def _popups(self, hwnd):
        import win32con
        import win32gui
        import win32process
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        windows = []
        def collect(candidate, _argument):
            if (win32gui.IsWindowVisible(candidate) and
                    win32process.GetWindowThreadProcessId(candidate)[1] == pid and
                    candidate != hwnd):
                windows.append(candidate)
        win32gui.EnumWindows(collect, None)
        if len(windows) > 64:
            raise ConsoleError('account_window_enumeration_failed')
        profiles = []
        for candidate in windows[:64]:
            if self._root(candidate).CurrentClassName == 'mmui::ProfileUniquePop':
                if win32gui.GetWindow(candidate, win32con.GW_OWNER) != hwnd:
                    raise ConsoleError('account_profile_identity_ambiguous')
                profiles.append(candidate)
        return profiles

    def _avatar(self, hwnd):
        root = self._root(hwnd, 'mmui::MainWindow')
        raw, uia = self.client._client, self.uia
        navs = root.FindAll(uia.TreeScope_Descendants,
                            raw.CreatePropertyCondition(uia.UIA_ClassNamePropertyId, 'mmui::MainTabBar'))
        if navs.Length != 1:
            raise ConsoleError('account_avatar_not_exposed')
        nav = navs.GetElement(0)
        avatars = nav.FindAll(uia.TreeScope_Descendants,
                              raw.CreatePropertyCondition(uia.UIA_ClassNamePropertyId, 'mmui::XAvatarImage'))
        if avatars.Length != 1:
            raise ConsoleError('account_avatar_not_exposed')
        avatar = avatars.GetElement(0)
        if avatar.CurrentProcessId != root.CurrentProcessId or avatar.CurrentIsOffscreen:
            raise ConsoleError('account_avatar_not_exposed')
        return avatar

    def _fields(self, popup, pid):
        root = self._root(popup, 'mmui::ProfileUniquePop')
        raw = self.client._client
        queue, seen, values = [root], set(), []
        while queue:
            item = queue.pop(0)
            identity = tuple(item.GetRuntimeId())
            if not identity or identity in seen or len(seen) >= 400 or item.CurrentProcessId != pid:
                raise ConsoleError('account_profile_tree_incomplete')
            seen.add(identity)
            value = item.CurrentName
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
            children = item.FindAll(self.uia.TreeScope_Children, raw.CreateTrueCondition())
            if children.Length > 30:
                raise ConsoleError('account_profile_tree_incomplete')
            queue.extend(children.GetElement(i) for i in range(children.Length))
        return values

    def own_profile(self, hwnd):
        import win32api
        import win32con
        import win32gui
        from comtypes.gen.UIAutomationClient import IUIAutomationInvokePattern
        from .account_discovery import profile_fields, window_in_live_use
        from .background import desktop_locked
        if window_in_live_use(hwnd):
            raise ConsoleError('account_discovery_client_busy')
        if desktop_locked():
            raise ConsoleError('desktop_locked_or_unavailable')
        if (win32api.GetTickCount() - win32api.GetLastInputInfo()) % 2**32 < 750:
            raise ConsoleError('account_discovery_human_takeover')
        stamp = self.stamp(hwnd)
        # An already-open contact card must never be promoted to the owner.
        if self._popups(hwnd):
            raise ConsoleError('account_discovery_existing_profile')
        receipt = dict(stamp=stamp, foreground=win32gui.GetForegroundWindow(),
                       placement=win32gui.GetWindowPlacement(hwnd), popup=None,
                       cursor_before=win32api.GetCursorPos(), last_input=win32api.GetLastInputInfo())
        self.checkpoint(receipt)
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            avatar = self._avatar(hwnd)
            if self.stamp(hwnd) != stamp:
                raise ConsoleError('account_discovery_instance_changed')
            if win32api.GetLastInputInfo() != receipt['last_input']:
                raise ConsoleError('account_discovery_human_takeover')
            pattern = avatar.GetCurrentPattern(self.uia.UIA_InvokePatternId)
            if not pattern:
                raise ConsoleError('account_avatar_not_exposed')
            pattern.QueryInterface(IUIAutomationInvokePattern).Invoke()
            receipt['last_input'] = win32api.GetLastInputInfo()
            receipt['cursor_after'] = win32api.GetCursorPos()
            self.checkpoint(receipt)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                popups = self._popups(hwnd)
                if len(popups) == 1:
                    receipt['popup'] = popups[0]
                    self.checkpoint(receipt)
                    break
                if len(popups) > 1:
                    raise ConsoleError('account_profile_identity_ambiguous')
                time.sleep(0.05)
            if not receipt['popup']:
                raise ConsoleError('account_discovery_profile_not_opened')
            first = self._fields(receipt['popup'], stamp[0])
            second = self._fields(receipt['popup'], stamp[0])
            if profile_fields(first) != profile_fields(second) or self.stamp(hwnd) != stamp:
                raise ConsoleError('account_discovery_instance_changed')
            return second
        finally:
            restore_window(receipt)
            self.checkpoint(None)
