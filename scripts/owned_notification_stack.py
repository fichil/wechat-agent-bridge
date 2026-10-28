"""Bounded notification stacks in the current synthetic process only.

No PID argument, debugger, process reopening, pointer write or target memory
inspection. Persist only allowlisted system module names/RVAs and file hashes.
"""
import ctypes as c
import hashlib
import os
from pathlib import Path
import threading

MODULES = ('UIAutomationCore.dll', 'user32.dll', 'win32u.dll', 'combase.dll',
           'oleacc.dll', 'ntdll.dll', 'KernelBase.dll')
# The first bounded native run filled all 48 slots inside Python/GUI dispatch.
# Keep a fixed 96-frame ceiling and explicitly mark hitting that ceiling.
MAX_FRAMES = 96


def normalize_frames(addresses, modules):
    if not isinstance(addresses, (list, tuple)) or len(addresses) > MAX_FRAMES:
        raise ValueError('stack_frame_limit')
    frames, unknown = [], 0
    for address in addresses:
        if type(address) is not int or not 0x10000 <= address < 0x800000000000:
            raise ValueError('stack_frame_invalid')
        matches = [m for m in modules if m['base'] <= address < m['base'] + m['size']]
        if len(matches) > 1:
            raise ValueError('stack_module_overlap')
        if matches:
            module = matches[0]
            if module['name'] not in MODULES:
                raise ValueError('stack_module_unverified')
            frames.append(dict(module=module['name'], rva=address - module['base']))
        else:
            unknown += 1
    return dict(frames=frames, unknown_frames=unknown, frame_count=len(addresses),
                frame_limit_reached=len(addresses) == MAX_FRAMES)


class OwnStackCapture:
    def __init__(self):
        if os.name != 'nt' or c.sizeof(c.c_void_p) != 8:
            raise RuntimeError('stack_windows_x64_required')
        self.kernel = c.WinDLL('kernel32', use_last_error=True)
        self.ntdll = c.WinDLL('ntdll')
        self.modules, self.fingerprints = [], {}
        get_module = self.kernel.GetModuleHandleW
        get_module.argtypes, get_module.restype = [c.c_wchar_p], c.c_void_p
        get_path = self.kernel.GetModuleFileNameW
        get_path.argtypes, get_path.restype = [c.c_void_p, c.c_wchar_p, c.c_uint32], c.c_uint32
        get_process = self.kernel.GetCurrentProcess
        get_process.argtypes, get_process.restype = [], c.c_void_p

        class ModuleInfo(c.Structure):
            _fields_ = [('base', c.c_void_p), ('size', c.c_uint32), ('entry', c.c_void_p)]

        get_info = self.kernel.K32GetModuleInformation
        get_info.argtypes = [c.c_void_p, c.c_void_p, c.POINTER(ModuleInfo), c.c_uint32]
        get_info.restype = c.c_int
        system = (Path(os.environ['SystemRoot']) / 'System32').resolve()
        for name in MODULES:
            handle = get_module(name)
            if not handle:
                continue  # Do not load modules merely to produce a stack label.
            buffer, info = c.create_unicode_buffer(32768), ModuleInfo()
            length = get_path(handle, buffer, len(buffer))
            if not length or length >= len(buffer) or not get_info(get_process(), handle, c.byref(info), c.sizeof(info)):
                raise RuntimeError('stack_module_identity_unavailable')
            path = Path(buffer.value).resolve()
            if path.parent != system or path.name.lower() != name.lower() or info.base != handle or not 0 < info.size <= 32 * 1024**2:
                raise RuntimeError('stack_module_identity_mismatch')
            self.modules.append(dict(name=name, base=handle, size=info.size))
            self.fingerprints[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        if 'UIAutomationCore.dll' not in self.fingerprints:
            raise RuntimeError('stack_uia_not_loaded')
        self.capture = self.ntdll.RtlCaptureStackBackTrace
        self.capture.argtypes = [c.c_uint32, c.c_uint32, c.POINTER(c.c_void_p), c.POINTER(c.c_uint32)]
        self.capture.restype = c.c_uint16

    def record(self):
        addresses = (c.c_void_p * MAX_FRAMES)()
        count = self.capture(0, MAX_FRAMES, addresses, None)
        if not 0 < count <= MAX_FRAMES:
            raise RuntimeError('stack_capture_incomplete')
        result = normalize_frames(list(addresses[:count]), self.modules)
        result['thread_id'] = threading.get_native_id()
        return result
