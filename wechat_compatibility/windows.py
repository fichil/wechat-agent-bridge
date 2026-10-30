"""Metadata-only module enumeration; never loads or calls a client module."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path


class Module(c.Structure):
    _fields_ = [('size',w.DWORD),('module_id',w.DWORD),('pid',w.DWORD),
                ('global_usage',w.DWORD),('usage',w.DWORD),('base',c.c_void_p),
                ('image_size',w.DWORD),('module',w.HMODULE),('name',w.WCHAR*256),('path',w.WCHAR*260)]


def core_path(pid):
    k = c.WinDLL('kernel32', use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes = [w.DWORD,w.DWORD]
    k.CreateToolhelp32Snapshot.restype = w.HANDLE
    k.CloseHandle.argtypes = [w.HANDLE]
    for name in ('Module32FirstW','Module32NextW'):
        getattr(k,name).argtypes = [w.HANDLE,c.POINTER(Module)]
    handle = k.CreateToolhelp32Snapshot(0x18,pid)
    if handle == c.c_void_p(-1).value:
        raise OSError('client_modules_unreadable')
    try:
        item = Module(); item.size = c.sizeof(item)
        found = k.Module32FirstW(handle,c.byref(item))
        while found:
            if item.name.lower() == 'weixin.dll':
                return Path(item.path)
            found = k.Module32NextW(handle,c.byref(item))
        return None
    finally:
        k.CloseHandle(handle)
