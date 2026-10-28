"""Current-user Windows DPAPI; no plaintext persistence fallback."""

import ctypes
from ctypes import wintypes
import os
from typing import Protocol


class Protector(Protocol):
    def protect(self, data: bytes) -> bytes: ...
    def unprotect(self, data: bytes) -> bytes: ...


class DPAPIProtector:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("windows_dpapi_required")

    @staticmethod
    def _call(data: bytes, decrypt: bool) -> bytes:
        class Blob(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        source = Blob(len(data), buf)
        dest = Blob()
        api = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        function = api.CryptUnprotectData if decrypt else api.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        function.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest)):
            raise RuntimeError("local_protection_failed")
        try:
            return ctypes.string_at(dest.data, dest.size)
        finally:
            kernel.LocalFree(dest.data)

    def protect(self, data: bytes) -> bytes:
        return self._call(data, False)

    def unprotect(self, data: bytes) -> bytes:
        return self._call(data, True)
