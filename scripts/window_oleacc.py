"""Bound-window OBJID_CLIENT interface presence and owner check only."""
from wechat_agent_bridge.console_models import ConsoleError


class WindowOleaccPort:
    def __init__(self, fp):
        import ctypes as c
        from ctypes import wintypes as w
        from comtypes import GUID
        from comtypes.gen.Accessibility import IAccessible
        self.c, self.w, self.interface, self.fp = c, w, IAccessible, fp
        self.api = c.WinDLL('oleacc', use_last_error=True)
        self.user = c.WinDLL('user32', use_last_error=True)
        self.api.AccessibleObjectFromWindow.argtypes = [w.HWND, w.DWORD, c.POINTER(GUID), c.POINTER(c.c_void_p)]
        self.api.AccessibleObjectFromWindow.restype = c.c_int32
        self.api.WindowFromAccessibleObject.argtypes = [c.POINTER(IAccessible), c.POINTER(w.HWND)]
        self.api.WindowFromAccessibleObject.restype = c.c_int32
        self.user.IsWindow.argtypes, self.user.IsWindow.restype = [w.HWND], w.BOOL
        self.user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
        self.user.GetWindowThreadProcessId.restype = w.DWORD
        self.user.GetAncestor.argtypes, self.user.GetAncestor.restype = [w.HWND, w.UINT], w.HWND

    def require_window(self, hwnd):
        if type(hwnd) is not int or hwnd <= 0 or not self.user.IsWindow(hwnd):
            raise ConsoleError('oleacc_selected_window_missing')
        pid = self.w.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, self.c.byref(pid))
        if pid.value != self.fp['pid'] or self.user.GetAncestor(hwnd, 2) != self.fp['hwnd']:
            raise ConsoleError('oleacc_window_outside_selected_root')

    def create(self, hwnd):
        self.require_window(hwnd)
        address = self.c.c_void_p()
        code = int(self.api.AccessibleObjectFromWindow(hwnd, 0xfffffffc,
            self.c.byref(self.interface._iid_), self.c.byref(address)))
        # Do not wrap an undefined output as a COM pointer on HRESULT failure.
        if code != 0 or not address.value:
            return code, None
        return code, self.c.cast(address, self.c.POINTER(self.interface))

    def owner(self, accessible):
        hwnd = self.w.HWND()
        code = int(self.api.WindowFromAccessibleObject(accessible, self.c.byref(hwnd)))
        return code, int(hwnd.value or 0)


def inspect_window(port, hwnd):
    port.require_window(hwnd)
    code, accessible = port.create(hwnd)
    result = dict(factory_hresult=code, interface_available=False, scope_verified=False,
                  interface_content_read=False, hit_test_called=False, action_executed=False)
    if code != 0:
        result['status'] = 'factory_failed'
    elif not accessible:
        result['status'] = 'factory_null'
    else:
        result['interface_available'] = True
        owner_code, owner_hwnd = port.owner(accessible)
        result['owner_hresult'] = owner_code
        if owner_code != 0 or not owner_hwnd:
            result['status'] = 'object_window_unavailable'
        else:
            try:
                port.require_window(owner_hwnd)
            except ConsoleError:
                result['status'] = 'object_outside_selected_root'
            else:
                result.update(status='scoped_interface_available', scope_verified=True,
                              object_window_hwnd=owner_hwnd)
    port.require_window(hwnd)
    return result


def worker(pipe, fp, _account):
    import sys
    sys.coinit_flags = 0
    try:
        from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
        from scripts.development_window_restore import WindowPort
        client = BackgroundUIAClient()
        root = client.element_from_handle(fp['hwnd'])
        def check():
            if (root.CurrentProcessId != fp['pid'] or root.CurrentNativeWindowHandle != fp['hwnd'] or
                    root.CurrentClassName != 'mmui::MainWindow'):
                raise ConsoleError('oleacc_selected_root_changed')
        check()
        window = WindowPort(fp['hwnd'])
        state = window.state()
        result = inspect_window(WindowOleaccPort(fp), fp['hwnd'])
        check()
        if window.state() != state:
            raise ConsoleError('oleacc_selected_window_state_changed')
        client.require_background_mode()
        pipe.send(dict(kind='navigation_metadata', metadata=dict(window_state=state,
            window_oleacc=result, normal_geometry_required=False, own_avatar_identity_verified=False,
            names_or_text_or_values_read=False, avatar_images_read=False, action_executed=False)))
    except Exception as exc:
        pipe.send(dict(kind='error', reason=str(exc) if isinstance(exc, ConsoleError) else 'window_oleacc_probe_failed'))
    finally:
        pipe.close()
