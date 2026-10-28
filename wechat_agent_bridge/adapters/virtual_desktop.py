"""Read-only public virtual-desktop queries; no enumeration or window actions.

Construct, query and close on one thread. The caller supplies already verified
window handles. These snapshots cannot prove continuous input isolation.
"""
import ctypes as c
import os
from pathlib import Path
import threading
import uuid

from ..console_models import ConsoleError


CLSID = uuid.UUID('aa509086-5ca9-4c25-8f95-589d3c07b48a')
IID = uuid.UUID('a5cd92ff-29be-454c-8d04-d82879fb3f1b')
GUID = c.c_ubyte * 16
HRESULT, BOOL = c.c_int32, c.c_int32


def _hwnd(value):
    if type(value) is not int or not 0 < value < 2 ** (8 * c.sizeof(c.c_void_p)):
        raise ConsoleError('virtual_desktop_invalid_hwnd')
    return value


def _desktop_id(value):
    try:
        if not isinstance(value, str):
            raise ValueError
        parsed = uuid.UUID(value)
        if not parsed.int:
            raise ValueError
        return str(parsed)
    except (ValueError, TypeError, AttributeError):
        raise ConsoleError('virtual_desktop_invalid_id') from None


def _snapshot(value):
    if not isinstance(value, dict) or type(value.get('current')) is not bool:
        raise ConsoleError('virtual_desktop_invalid_snapshot')
    return {'hwnd': _hwnd(value.get('hwnd')), 'desktop_id': _desktop_id(value.get('desktop_id')),
            'current': value['current']}


def validate_initial(target, foreground):
    """Bind a noncurrent target and the current foreground's exact HWND/GUID."""
    target, foreground = _snapshot(target), _snapshot(foreground)
    if (target['current'] is not False or foreground['current'] is not True or
            target['hwnd'] == foreground['hwnd'] or target['desktop_id'] == foreground['desktop_id']):
        raise ConsoleError('virtual_desktop_initial_separation_unverified')
    return {'target': target, 'foreground': foreground}


def unchanged_snapshot(initial, target, foreground):
    """Require the original target and foreground to retain their exact states."""
    if not isinstance(initial, dict):
        raise ConsoleError('virtual_desktop_invalid_snapshot')
    bound = validate_initial(initial.get('target'), initial.get('foreground'))
    current = validate_initial(target, foreground)
    if current != bound:
        raise ConsoleError('virtual_desktop_snapshot_changed')
    return True


def validate_current_target(target):
    """Explicit same-desktop mode; foreground/focus are guarded separately.

    Does not query or infer a desktop for the shell foreground window, which
    may have no per-desktop GUID. This is not evidence of desktop separation.
    """
    target = _snapshot(target)
    if target['current'] is not True:
        raise ConsoleError('current_desktop_target_required')
    return {'target':target,'mode':'current_desktop_background'}


def unchanged_current_target(initial, target):
    current=validate_current_target(target)
    if current != initial:
        raise ConsoleError('current_desktop_target_changed')
    return True


def _load_api():
    if os.name != 'nt':
        raise ConsoleError('virtual_desktop_windows_required')
    api = c.WinDLL(str(Path(os.environ['SystemRoot']) / 'System32/ole32.dll'), winmode=0x800)
    api.CoInitializeEx.argtypes, api.CoInitializeEx.restype = [c.c_void_p, c.c_uint32], HRESULT
    api.CoCreateInstance.argtypes = [c.POINTER(GUID), c.c_void_p, c.c_uint32,
                                    c.POINTER(GUID), c.POINTER(c.c_void_p)]
    api.CoCreateInstance.restype = HRESULT
    api.CoUninitialize.argtypes, api.CoUninitialize.restype = [], None
    return api


def _bind(pointer, index, result, *arguments):
    # Only Release and the two documented read-only slots are ever requested.
    if index not in (2, 3, 4):
        raise ConsoleError('virtual_desktop_interface_invalid')
    table = c.cast(pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
    address = table[index]
    if not address:
        raise ConsoleError('virtual_desktop_interface_invalid')
    return getattr(c, 'WINFUNCTYPE', c.CFUNCTYPE)(result, c.c_void_p, *arguments)(address)


class _NativeDesktopManager:
    def __init__(self, api=None, binder=None):
        self._thread, self._pointer = threading.get_ident(), c.c_void_p()
        self._initialized, self._release, self._closed = False, None, False
        self._close_failed = False
        self._api, bind = api if api is not None else _load_api(), binder or _bind
        try:
            # S_FALSE still increments this thread's COM initialization count.
            if self._api.CoInitializeEx(None, 0) not in (0, 1):
                raise ConsoleError('virtual_desktop_com_init_failed')
            self._initialized = True
            clsid, iid = GUID.from_buffer_copy(CLSID.bytes_le), GUID.from_buffer_copy(IID.bytes_le)
            if self._api.CoCreateInstance(c.byref(clsid), None, 23, c.byref(iid), c.byref(self._pointer)) != 0:
                raise ConsoleError('virtual_desktop_create_failed')
            if not self._pointer.value:
                raise ConsoleError('virtual_desktop_create_failed')
            self._release = bind(self._pointer, 2, c.c_uint32)
            self._is_current = bind(self._pointer, 3, HRESULT, c.c_void_p, c.POINTER(BOOL))
            self._get_id = bind(self._pointer, 4, HRESULT, c.c_void_p, c.POINTER(GUID))
        except Exception:
            self.close()
            raise

    def _require_thread(self):
        if threading.get_ident() != self._thread:
            raise ConsoleError('virtual_desktop_wrong_thread')

    def query(self, hwnd):
        self._require_thread()
        if self._closed:
            raise ConsoleError('virtual_desktop_closed')
        current, desktop = BOOL(-1), GUID()
        if self._is_current(self._pointer, hwnd, c.byref(current)) != 0 or current.value not in (0, 1):
            raise ConsoleError('virtual_desktop_query_failed')
        if self._get_id(self._pointer, hwnd, c.byref(desktop)) != 0:
            raise ConsoleError('virtual_desktop_query_failed')
        return {'desktop_id': str(uuid.UUID(bytes_le=bytes(desktop))), 'current': bool(current.value)}

    def close(self):
        self._require_thread()
        if self._closed:
            if self._close_failed:
                raise ConsoleError('virtual_desktop_close_failed')
            return True
        self._closed = True
        failed = False
        try:
            if self._pointer.value and self._release is not None:
                pointer, self._pointer = self._pointer, c.c_void_p()
                self._release(pointer)
        except Exception:
            failed = True
        finally:
            if self._initialized:
                self._initialized = False
                try:
                    self._api.CoUninitialize()
                except Exception:
                    failed = True
        if failed:
            self._close_failed = True
            raise ConsoleError('virtual_desktop_close_failed')
        return True


class VirtualDesktopReader:
    """Own one query-only COM object; factory is solely for synthetic tests."""
    def __init__(self, factory=None):
        self._thread, self._closed = threading.get_ident(), False
        self._close_failed = False
        try:
            self._manager = (factory or _NativeDesktopManager)()
        except Exception:
            raise ConsoleError('virtual_desktop_unavailable') from None

    def _require_open(self):
        if threading.get_ident() != self._thread:
            raise ConsoleError('virtual_desktop_wrong_thread')
        if self._closed:
            raise ConsoleError('virtual_desktop_closed')

    def query(self, hwnd):
        self._require_open()
        _hwnd(hwnd)
        try:
            result = self._manager.query(hwnd)
            if not isinstance(result, dict) or type(result.get('current')) is not bool:
                raise ValueError
            return {'desktop_id': _desktop_id(result.get('desktop_id')), 'current': result['current']}
        except Exception:
            raise ConsoleError('virtual_desktop_query_failed') from None

    def close(self):
        if threading.get_ident() != self._thread:
            raise ConsoleError('virtual_desktop_wrong_thread')
        if self._closed:
            if self._close_failed:
                raise ConsoleError('virtual_desktop_close_failed')
            return True
        self._closed = True
        try:
            if self._manager.close() is not True:
                raise ValueError
        except Exception:
            self._close_failed = True
            raise ConsoleError('virtual_desktop_close_failed') from None
        return True
