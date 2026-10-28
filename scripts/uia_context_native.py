"""Current-process-only memory port for the exact UIAutomationCore image.

No external PID/handle argument, process opening, writes or internal calls.
Construction occurs only inside an explicitly enabled native research child.
"""
import ctypes as c
import hashlib
import os
from pathlib import Path
import threading

from scripts.uia_context_evidence import ContextError, DLL_SHA256, readable_region
from wechat_agent_bridge.adapters.pe_inventory import PE


class Region(c.Structure):
    _fields_ = [('base', c.c_void_p), ('allocation', c.c_void_p),
               ('allocation_protect', c.c_uint32), ('size', c.c_size_t),
               ('state', c.c_uint32), ('protect', c.c_uint32), ('type', c.c_uint32)]


class SelfMemory:
    def __init__(self):
        if os.name != 'nt' or c.sizeof(c.c_void_p) != 8 or c.sizeof(Region) != 48:
            raise ContextError('windows_x64_required')
        self.thread, self.closed = threading.get_ident(), False
        self.path = Path(os.environ['SystemRoot']) / 'System32/UIAutomationCore.dll'
        data = self.path.read_bytes()
        if hashlib.sha256(data).hexdigest() != DLL_SHA256:
            raise ContextError('image_fingerprint_mismatch')
        pe = PE(data)
        self.k = c.WinDLL(str(self.path.parent / 'kernel32.dll'), winmode=0x800)
        self.k.GetCurrentProcess.argtypes, self.k.GetCurrentProcess.restype = [], c.c_void_p
        self.k.GetModuleHandleW.argtypes, self.k.GetModuleHandleW.restype = [c.c_wchar_p], c.c_void_p
        self.k.GetModuleFileNameW.argtypes = [c.c_void_p, c.c_wchar_p, c.c_uint32]
        self.k.GetModuleFileNameW.restype = c.c_uint32
        self.k.VirtualQuery.argtypes = [c.c_void_p, c.POINTER(Region), c.c_size_t]
        self.k.VirtualQuery.restype = c.c_size_t
        self.k.ReadProcessMemory.argtypes = [c.c_void_p, c.c_void_p, c.c_void_p,
                                            c.c_size_t, c.POINTER(c.c_size_t)]
        self.k.ReadProcessMemory.restype = c.c_int
        # This pseudo-handle is obtained here, never supplied by a caller.
        self.process = self.k.GetCurrentProcess()
        base = self.k.GetModuleHandleW('UIAutomationCore.dll')
        if not base:
            raise ContextError('uia_image_not_loaded')
        self.image = {'base': base, 'size': pe.unpack('<I', pe.directories - 56)[0],
                      'sha256': DLL_SHA256}
        self.validate_source()

    def require_open(self):
        if self.closed or threading.get_ident() != self.thread:
            raise ContextError('self_memory_closed_or_wrong_thread')

    def validate_source(self):
        self.require_open()
        path = c.create_unicode_buffer(1024)
        count = self.k.GetModuleFileNameW(self.image['base'], path, len(path))
        if (not 0 < count < len(path) or Path(path.value).resolve() != self.path.resolve() or
                self.k.GetModuleHandleW('UIAutomationCore.dll') != self.image['base'] or
                hashlib.sha256(self.path.read_bytes()).hexdigest() != DLL_SHA256):
            raise ContextError('loaded_image_identity_changed')

    def read(self, address, count, *, image=False):
        self.require_open()
        region = Region()
        if self.k.VirtualQuery(address, c.byref(region), c.sizeof(region)) != c.sizeof(region):
            raise ContextError('self_memory_region_unavailable')
        values = {key: int(getattr(region, key) or 0) for key in
                  ('base', 'allocation', 'size', 'state', 'protect', 'type')}
        if not readable_region(address, count, values,
                               image_base=self.image['base'] if image else None):
            raise ContextError('self_memory_region_unreadable')
        raw, used = c.create_string_buffer(count), c.c_size_t()
        if (not self.k.ReadProcessMemory(self.process, address, raw, count, c.byref(used)) or
                used.value != count):
            raise ContextError('self_memory_read_failed')
        return raw.raw

    def close(self):
        self.require_open()
        self.closed = True  # No opened process or module handle to release.
