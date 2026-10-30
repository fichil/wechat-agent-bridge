"""Current-user Windows DPAPI. Never fall back to plaintext or show a prompt."""
import ctypes
from ctypes import wintypes
import os

from .conversations import ConversationError


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _transform(value: bytes, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise ConversationError("secret_protection_unavailable", "密钥加密仅支持 Windows，请在 Windows 本机保存。", 503)
    source = ctypes.create_string_buffer(value)
    incoming = Blob(len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
            code = "api_key_unreadable" if decrypt else "secret_protection_failed"
            message = "密钥无法由当前 Windows 用户读取，请重新填写。" if decrypt else "密钥加密失败，配置未保存。"
            raise ConversationError(code, message, 409 if decrypt else 503)
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        ctypes.memset(source, 0, len(source))
        if outgoing.data:
            ctypes.memset(outgoing.data, 0, outgoing.size)
            kernel.LocalFree(outgoing.data)


def protect(value: str) -> bytes:
    return _transform(value.encode("utf-8"), False)


def reveal(value: bytes) -> str:
    try:
        result = _transform(value, True).decode("utf-8")
        if not result:
            raise ValueError()
        return result
    except (UnicodeError, ValueError):
        raise ConversationError("api_key_unreadable", "密钥无法读取，请重新填写。", 409) from None


def verify(value: bytes) -> bool:
    try:
        return bool(_transform(value, True).decode("utf-8"))
    except (ConversationError, UnicodeError):
        return False
