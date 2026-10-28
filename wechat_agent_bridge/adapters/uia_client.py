"""A dedicated UIA client that disables UIA's automatic pre-action focus.

This is a client-side setting, not a window or system focus change. It does not
prevent a provider from activating its own window and confers no live authority.
Create this object on its COM worker thread; never reuse pywinauto's elements or
patterns with it. Production read/send compatibility remains separately gated.
"""
import threading

from ..console_models import ConsoleError


def _create_automation2():
    # Imported only by an explicitly created native worker, never at test import.
    import comtypes
    import comtypes.client
    from comtypes.gen import UIAutomationClient as uia

    return comtypes.CoCreateInstance(
        uia.CUIAutomation8._reg_clsid_, interface=uia.IUIAutomation2,
        clsctx=comtypes.CLSCTX_INPROC_SERVER,
    )


def _read_auto_focus(client):
    try:
        value = client.AutoSetFocus
    except Exception:
        raise ConsoleError("uia_auto_focus_read_failed") from None
    # COM BOOL is an integer; do not accept a missing/dynamic proxy or string.
    if type(value) not in (bool, int):
        raise ConsoleError("uia_auto_focus_invalid_value")
    return value != 0


class BackgroundUIAClient:
    """Fresh CUIAutomation8 with a verified FALSE AutoSetFocus property.

    The factory argument is for synthetic tests. No roots, elements, patterns or
    actions are requested during initialization. An unsupported or failed setting
    fails closed; there is no fallback to a default-focus client.
    """

    def __init__(self, factory=None):
        self._thread = threading.get_ident()
        try:
            self._client = (factory or _create_automation2)()
        except Exception:
            raise ConsoleError("uia_automation2_unavailable") from None
        self.initial_auto_focus = _read_auto_focus(self._client)
        try:
            self._client.AutoSetFocus = 0
        except Exception:
            raise ConsoleError("uia_auto_focus_disable_failed") from None
        self.require_background_mode()

    def require_background_mode(self):
        if threading.get_ident() != self._thread:
            raise ConsoleError("uia_client_wrong_thread")
        if _read_auto_focus(self._client):
            raise ConsoleError("uia_auto_focus_not_disabled")

    def element_from_handle(self, hwnd):
        """Obtain an element from this client only; caller must verify identity."""
        self.require_background_mode()
        if type(hwnd) is not int or not 0 < hwnd < 2**64:
            raise ConsoleError("uia_invalid_window_handle")
        try:
            element = self._client.ElementFromHandle(hwnd)
        except Exception:
            raise ConsoleError("uia_element_unavailable") from None
        if not element:
            raise ConsoleError("uia_element_unavailable")
        return element
