"""Lazy Windows read-only primitives. No UI automation or target-write APIs."""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import struct
import time

from .models import Instance, ProbeError

CHUNK = 1024 * 1024
READ_RATE = 32 * CHUNK


class MBI(C.Structure):
    _fields_ = [('BaseAddress', C.c_void_p), ('AllocationBase', C.c_void_p),
                ('AllocationProtect', W.DWORD), ('PartitionId', W.WORD),
                ('RegionSize', C.c_size_t), ('State', W.DWORD),
                ('Protect', W.DWORD), ('Type', W.DWORD)]


def libraries():
    if os.name != 'nt' or C.sizeof(C.c_void_p) != 8:
        raise ProbeError('windows_x64_required')
    k = C.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.argtypes, k.OpenProcess.restype = [W.DWORD, W.BOOL, W.DWORD], W.HANDLE
    k.CloseHandle.argtypes = [W.HANDLE]
    k.GetProcessTimes.argtypes = [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4
    k.ProcessIdToSessionId.argtypes = [W.DWORD, C.POINTER(W.DWORD)]
    k.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t,
                                   C.POINTER(C.c_size_t)]
    k.VirtualQueryEx.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(MBI), C.c_size_t]
    k.VirtualQueryEx.restype = C.c_size_t
    k.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD,
                              W.DWORD, W.HANDLE]
    k.CreateFileW.restype = W.HANDLE
    k.GetFileSizeEx.argtypes = [W.HANDLE, C.POINTER(C.c_longlong)]
    k.ReadFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
    return k


def creation_time(k, handle):
    times = [W.FILETIME() for _ in range(4)]
    if not k.GetProcessTimes(handle, *(C.byref(t) for t in times)):
        raise ProbeError('process_time_unreadable')
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def handle_sid(k, handle):
    a = C.WinDLL('advapi32', use_last_error=True)
    a.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
    a.GetTokenInformation.argtypes = [W.HANDLE, W.DWORD, C.c_void_p, W.DWORD,
                                     C.POINTER(W.DWORD)]
    a.ConvertSidToStringSidW.argtypes = [C.c_void_p, C.POINTER(W.LPWSTR)]
    k.LocalFree.argtypes = [C.c_void_p]
    token, size = W.HANDLE(), W.DWORD()
    try:
        if not a.OpenProcessToken(handle, 8, C.byref(token)):
            raise ProbeError('process_owner_unreadable')
        a.GetTokenInformation(token, 1, None, 0, C.byref(size))
        if not 0 < size.value < 65536:
            raise ProbeError('process_owner_unreadable')
        buffer = C.create_string_buffer(size.value)
        if not a.GetTokenInformation(token, 1, buffer, size, C.byref(size)):
            raise ProbeError('process_owner_unreadable')
        sid_pointer = C.cast(buffer, C.POINTER(C.c_void_p))[0]
        text = W.LPWSTR()
        if not a.ConvertSidToStringSidW(sid_pointer, C.byref(text)):
            raise ProbeError('process_owner_unreadable')
        try:
            return text.value
        finally:
            k.LocalFree(C.cast(text, C.c_void_p))
    finally:
        if token:
            k.CloseHandle(token)


def process_sid(k, pid):
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ProbeError('process_owner_unreadable')
    try:
        return handle_sid(k, handle)
    finally:
        k.CloseHandle(handle)


def file_version(path):
    v = C.WinDLL('version', use_last_error=True)
    v.GetFileVersionInfoSizeW.argtypes = [W.LPCWSTR, C.POINTER(W.DWORD)]
    v.GetFileVersionInfoW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p]
    v.VerQueryValueW.argtypes = [C.c_void_p, W.LPCWSTR, C.POINTER(C.c_void_p),
                               C.POINTER(W.UINT)]
    size = v.GetFileVersionInfoSizeW(path, None)
    if not 0 < size < 4 * CHUNK:
        raise ProbeError('client_version_unreadable')
    buffer, pointer, length = C.create_string_buffer(size), C.c_void_p(), W.UINT()
    if (not v.GetFileVersionInfoW(path, 0, size, buffer) or
            not v.VerQueryValueW(buffer, '\\', C.byref(pointer), C.byref(length)) or length.value < 16):
        raise ProbeError('client_version_unreadable')
    fields = C.cast(pointer, C.POINTER(W.DWORD))
    ms, ls = fields[2], fields[3]
    return f'{ms >> 16}.{ms & 65535}.{ls >> 16}.{ls & 65535}'


def is_client_process(arguments):
    return not any(arg == '--type' or arg.startswith('--type=') or
                   arg.startswith('--crashpad-handler') for arg in arguments)


def enumerate_instances():
    import psutil
    k = libraries()
    sid = process_sid(k, os.getpid())
    result, issues = [], []
    for process in psutil.process_iter(['pid', 'name']):
        if (process.info['name'] or '').lower() not in ('weixin.exe', 'wechat.exe'):
            continue
        pid = process.pid
        handle = None
        try:
            if process_sid(k, pid) != sid:
                continue
            # Qt helper processes cannot constitute independent logged-in accounts.
            if not is_client_process(process.cmdline()):
                continue
            handle = k.OpenProcess(0x1000, False, pid)
            if not handle:
                raise ProbeError('process_metadata_unreadable')
            session = W.DWORD()
            if not k.ProcessIdToSessionId(pid, C.byref(session)):
                raise ProbeError('session_unreadable')
            result.append(Instance(pid, creation_time(k, handle), session.value,
                                   file_version(process.exe())))
        except psutil.NoSuchProcess:
            issues.append(dict(pid=pid, reason='process_exited_during_enumeration'))
        except (psutil.AccessDenied, OSError):
            issues.append(dict(pid=pid, reason='process_metadata_access_denied'))
        except ProbeError as exc:
            issues.append(dict(pid=pid, reason=str(exc)))
        finally:
            if handle:
                k.CloseHandle(handle)
    return sorted(result, key=lambda p: (p.session_id, p.pid)), issues


class ProcessMemory:
    def __init__(self, instance, deadline):
        self.instance, self.deadline = instance, deadline
        self.k = libraries()
        # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, never PROCESS_ALL_ACCESS.
        self.handle = self.k.OpenProcess(0x410, False, instance.pid)
        if not self.handle:
            raise ProbeError('process_read_access_denied')
        self.bytes_read, self.read_started = 0, time.monotonic()
        try:
            if handle_sid(self.k, self.handle) != process_sid(self.k, os.getpid()):
                raise ProbeError('process_owner_changed')
            self.check()
        except Exception:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None

    def check(self):
        if time.monotonic() >= self.deadline:
            raise ProbeError('instance_timeout')
        if creation_time(self.k, self.handle) != self.instance.created:
            raise ProbeError('process_instance_changed')
        code = W.DWORD()
        self.k.GetExitCodeProcess.argtypes = [W.HANDLE, C.POINTER(W.DWORD)]
        if not self.k.GetExitCodeProcess(self.handle, C.byref(code)) or code.value != 259:
            raise ProbeError('process_exited')

    def read(self, address, size):
        self.check()
        if not 0 < size <= CHUNK or not 0x10000 <= address < 0x800000000000:
            return b''
        delay = self.bytes_read / READ_RATE - (time.monotonic() - self.read_started)
        while delay > 0:
            time.sleep(min(delay, 0.05))
            self.check()
            delay = self.bytes_read / READ_RATE - (time.monotonic() - self.read_started)
        buffer, read = C.create_string_buffer(size), C.c_size_t()
        self.k.ReadProcessMemory(self.handle, address, buffer, size, C.byref(read))
        self.bytes_read += size  # failed/partial reads also consume the budget
        return buffer.raw[:read.value]

    def regions(self):
        address = 0
        while address < 0x800000000000:
            self.check()
            item = MBI()
            if not self.k.VirtualQueryEx(self.handle, address, C.byref(item), C.sizeof(item)):
                break
            end = (item.BaseAddress or 0) + item.RegionSize
            if end <= address:
                raise ProbeError('memory_region_invalid')
            if (item.State == 0x1000 and not item.Protect & (0x100 | 1) and
                    item.Protect & 0xff in (2, 4, 8, 0x20, 0x40, 0x80)):
                yield item
            address = end

    def find(self, needles, *, private_only=False, bounds=None, limit=1024, check=lambda: None):
        hits = {needle: [] for needle in needles}
        overlap = max(map(len, needles)) - 1
        for region in self.regions():
            check()
            if private_only and region.Type != 0x20000:
                continue
            start, end = region.BaseAddress, region.BaseAddress + region.RegionSize
            if bounds:
                start, end = max(start, bounds[0]), min(end, bounds[1])
            tail = b''
            for address in range(start, end, CHUNK):
                check()
                data = self.read(address, min(CHUNK, end-address))
                combined = tail + data
                origin = address - len(tail)
                for needle in needles:
                    pos = combined.find(needle)
                    while pos >= 0:
                        hit = origin + pos
                        if not hits[needle] or hits[needle][-1] != hit:
                            hits[needle].append(hit)
                            if len(hits[needle]) > limit:
                                raise ProbeError('memory_candidates_exceeded')
                        pos = combined.find(needle, pos+1)
                tail = combined[-overlap:] if len(data) == min(CHUNK, end-address) else b''
        return hits

    def mapped_paths(self):
        p = C.WinDLL('psapi', use_last_error=True)
        p.GetMappedFileNameW.argtypes = [W.HANDLE, C.c_void_p, W.LPWSTR, W.DWORD]
        self.k.QueryDosDeviceW.argtypes = [W.LPCWSTR, W.LPWSTR, W.DWORD]
        devices = []
        for drive in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            buffer = C.create_unicode_buffer(32768)
            if self.k.QueryDosDeviceW(drive + ':', buffer, len(buffer)):
                devices.append((buffer.value, drive + ':'))
        paths = {}
        for region in self.regions():
            if region.Type not in (0x40000, 0x1000000):
                continue
            buffer = C.create_unicode_buffer(32768)
            if not p.GetMappedFileNameW(self.handle, region.BaseAddress, buffer, len(buffer)):
                continue
            name = buffer.value
            for device, drive in sorted(devices, key=lambda pair: -len(pair[0])):
                if name.casefold().startswith(device.casefold() + '\\'):
                    name = drive + name[len(device):]
                    break
            paths.setdefault(name, region.AllocationBase)
        return paths


def read_shared_file(path, maximum, *, prefix=None, optional=False, check=lambda: None):
    check()
    k = libraries()
    handle = k.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x08000000, None)
    if handle in (None, C.c_void_p(-1).value):
        if C.get_last_error() in (2, 3):
            if optional:
                return b''
            raise FileNotFoundError
        raise ProbeError('database_read_access_denied')
    try:
        size = C.c_longlong()
        if not k.GetFileSizeEx(handle, C.byref(size)) or not 0 <= size.value <= maximum:
            raise ProbeError('database_size_limit')
        wanted = size.value if prefix is None else min(prefix, size.value)
        output = bytearray()
        file_started = time.monotonic()
        while len(output) < wanted:
            delay = len(output) / READ_RATE - (time.monotonic() - file_started)
            while delay > 0:
                time.sleep(min(delay, 0.05))
                check()
                delay = len(output) / READ_RATE - (time.monotonic() - file_started)
            check()
            buffer, received = C.create_string_buffer(min(CHUNK, wanted-len(output))), W.DWORD()
            if not k.ReadFile(handle, buffer, len(buffer), C.byref(received), None) or not received.value:
                raise ProbeError('database_read_changed')
            output.extend(buffer.raw[:received.value])
        return bytes(output)
    finally:
        k.CloseHandle(handle)
