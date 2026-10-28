"""Semantic-only send transaction; never injects input or uses the clipboard."""

from dataclasses import dataclass

from ..console_models import ConsoleError


@dataclass(frozen=True)
class DesktopSnapshot:
    foreground: int
    focus: int
    clipboard_sequence: int
    target_active: bool = False
    locked: bool = False


def desktop_locked():
    """Check accessibility of the input desktop without inspecting input/focus."""
    import ctypes as c
    from ctypes import wintypes as w
    user = c.WinDLL('user32', use_last_error=True)
    user.OpenInputDesktop.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    user.OpenInputDesktop.restype = w.HANDLE
    user.CloseDesktop.argtypes = [w.HANDLE]
    desktop = user.OpenInputDesktop(0, False, 0x0100)
    if not desktop:
        return True
    user.CloseDesktop(desktop)
    return False


def unchanged(before, after):
    if after.locked:
        raise ConsoleError("desktop_locked_or_unavailable")
    if after.target_active:
        raise ConsoleError("human_takeover")
    if (before.foreground, before.focus) != (after.foreground, after.focus):
        raise ConsoleError("background_focus_changed")
    if before.clipboard_sequence != after.clipboard_sequence:
        raise ConsoleError("background_clipboard_changed")


def snapshot(target_pid):
    import ctypes as c
    from ctypes import wintypes as w
    u = c.WinDLL("user32", use_last_error=True)
    u.GetForegroundWindow.restype = w.HWND
    u.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
    u.GetClipboardSequenceNumber.restype = w.DWORD
    u.OpenInputDesktop.argtypes, u.OpenInputDesktop.restype = [w.DWORD, w.BOOL, w.DWORD], w.HANDLE
    u.CloseDesktop.argtypes = [w.HANDLE]
    class GUI(c.Structure):
        _fields_ = [("cbSize", w.DWORD), ("flags", w.DWORD), ("hwndActive", w.HWND),
                    ("hwndFocus", w.HWND), ("hwndCapture", w.HWND), ("hwndMenuOwner", w.HWND),
                    ("hwndMoveSize", w.HWND), ("hwndCaret", w.HWND), ("rcCaret", w.RECT)]
    u.GetGUIThreadInfo.argtypes = [w.DWORD, c.POINTER(GUI)]
    foreground, pid = u.GetForegroundWindow(), w.DWORD()
    thread = u.GetWindowThreadProcessId(foreground, c.byref(pid))
    gui = GUI(); gui.cbSize = c.sizeof(GUI)
    if not foreground or not thread or not u.GetGUIThreadInfo(thread, c.byref(gui)):
        raise ConsoleError("background_focus_unavailable")
    desktop = u.OpenInputDesktop(0, False, 0x0100)
    if desktop:
        u.CloseDesktop(desktop)
    return DesktopSnapshot(foreground, gui.hwndFocus or 0, u.GetClipboardSequenceNumber(), pid.value == target_pid, not desktop)


def input_timestamp():
    """Input epoch only; never reads keys or installs hooks."""
    import ctypes as c
    from ctypes import wintypes as w
    class LastInput(c.Structure):
        _fields_=[('cbSize',w.UINT),('dwTime',w.DWORD)]
    user=c.WinDLL('user32',use_last_error=True)
    user.GetLastInputInfo.argtypes=[c.POINTER(LastInput)]
    user.GetLastInputInfo.restype=w.BOOL
    info=LastInput();info.cbSize=c.sizeof(info)
    if not user.GetLastInputInfo(c.byref(info)):
        raise ConsoleError('desktop_input_timestamp_unavailable')
    return int(info.dwTime)


class BackgroundSender:
    """Port supplies verified target, exact draft and own-message readback.

    A UIA pattern existing is NOT sufficient to construct a live port. Its
    account, conversation and readback semantics must already be validated.
    The caller durably claims the request and budget before invoking run().
    """
    def __init__(self, port, observer=None):
        self.port = port
        self.observer = observer

    def run(self, account, conversation, text, request_id, checkpoint=lambda: None):
        port, invoked, staged, baseline = self.port, False, False, None
        def phase(name):
            if self.observer is not None:
                self.observer.set_phase(name)
        def check():
            checkpoint()  # Rechecks authorization, pause epoch and persistent task.
            if self.observer is not None:
                self.observer.check()
            if port.identity() != (account, conversation):
                raise ConsoleError("target_mismatch")
            unchanged(baseline, port.snapshot())
        try:
            if self.observer is None and not getattr(port, "simulation_only", False):
                raise ConsoleError("desktop_event_observation_required")
            if not port.capable():
                raise ConsoleError("background_send_not_verified")
            phase("preflight")
            baseline = port.snapshot()
            check()
            if port.draft() != "":
                raise ConsoleError("existing_draft_or_unknown")
            prior = port.own_message_ids()
            check()
            # Mark staged before COM call: a timeout may still have set the value.
            staged = True
            phase("set_value")
            port.set_value(text)
            check()
            if port.draft() != text:
                raise ConsoleError("background_text_readback_mismatch")
            check()
            if port.draft() != text:
                raise ConsoleError("draft_changed_before_send")
            invoked = True  # Any exception from Invoke has an uncertain outcome.
            phase("invoke_send")
            port.invoke_send()
            check()
            phase("send_readback")
            confirmed = port.confirm_own_message(text, prior)
            check()
            if not confirmed:
                raise ConsoleError("background_send_readback_missing")
            return {"status": "ui_confirmed", "reason": "background_ui_confirmed", "attempted": True}
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ConsoleError) else "background_operation_failed"
            # Never erase a user's draft or clean after an uncertain Invoke.
            if staged and not invoked:
                try:
                    check()
                    if port.draft() == text:
                        check()
                        phase("cleanup")
                        port.set_value("")
                except Exception:
                    pass
            return {"status": "unknown" if invoked else "failed", "reason": reason, "attempted": invoked}
