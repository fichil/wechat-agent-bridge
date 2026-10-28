"""Opt-in research transport; not a production navigation capability.

Targets one freshly checked client-area rectangle. No SendInput, cursor moves,
activation, keyboard, clipboard, window-style changes or client patching.
"""
from ..console_models import ConsoleError


def center(rect, client_rect):
    if (len(rect) != 4 or len(client_rect) != 4 or
            any(type(x) is not int for x in (*rect, *client_rect))):
        raise ConsoleError('navigation_rectangle_invalid')
    l, t, r, b = rect
    cl, ct, cr, cb = client_rect
    if not (0 <= cl <= l < r <= cr <= 32767 and 0 <= ct <= t < b <= cb <= 32767):
        raise ConsoleError('navigation_rectangle_outside_client')
    return ((l+r)//2, (t+b)//2)


class WindowMessages:
    """Timeout is transport failure, never proof that the UI did nothing."""
    def __init__(self, hwnd, pid):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w, self.hwnd, self.pid = c, w, hwnd, pid
        self.u = u = c.WinDLL('user32', use_last_error=True)
        u.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
        u.GetWindowThreadProcessId.restype = w.DWORD
        u.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM,
                                        w.UINT, w.UINT, c.POINTER(c.c_size_t)]
        u.SendMessageTimeoutW.restype = w.LPARAM
        u.GetClientRect.argtypes = [w.HWND, c.POINTER(w.RECT)]
        u.ScreenToClient.argtypes = [w.HWND, c.POINTER(w.POINT)]
        u.IsIconic.argtypes = [w.HWND]
        self.thread = self.check()

    def check(self):
        actual = self.w.DWORD()
        thread = self.u.GetWindowThreadProcessId(self.hwnd, self.c.byref(actual))
        if not thread or actual.value != self.pid or self.hwnd in (0, 0xffff):
            raise ConsoleError('navigation_window_changed')
        if getattr(self, 'thread', thread) != thread:
            raise ConsoleError('navigation_thread_changed')
        return thread

    def point(self, screen_rect):
        self.check()
        if self.u.IsIconic(self.hwnd):
            raise ConsoleError('navigation_minimized_geometry_unavailable')
        a, b = self.w.POINT(*screen_rect[:2]), self.w.POINT(*screen_rect[2:])
        rect = self.w.RECT()
        if not (self.u.ScreenToClient(self.hwnd, self.c.byref(a)) and
                self.u.ScreenToClient(self.hwnd, self.c.byref(b)) and
                self.u.GetClientRect(self.hwnd, self.c.byref(rect))):
            raise ConsoleError('navigation_client_geometry_failed')
        return center((a.x, a.y, b.x, b.y), (rect.left, rect.top, rect.right, rect.bottom))

    def send(self, message, wparam, lparam, timeout_ms=500):
        self.check()
        if message not in (0x201, 0x202) or timeout_ms not in range(1, 1001):
            raise ConsoleError('navigation_message_not_allowed')
        value = self.c.c_size_t()
        self.c.set_last_error(0)
        if not self.u.SendMessageTimeoutW(self.hwnd, message, wparam, lparam,
                                         0x22, timeout_ms, self.c.byref(value)):
            raise ConsoleError('navigation_message_timeout_or_failure')

    def click(self, point):
        x, y = point
        if any(type(v) is not int or not 0 <= v <= 32767 for v in (x, y)):
            raise ConsoleError('navigation_point_invalid')
        packed = x | (y << 16)
        self.send(0x201, 1, packed)
        self.send(0x202, 0, packed)
