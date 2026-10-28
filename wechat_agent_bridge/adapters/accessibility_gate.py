"""Opt-in, single-byte Qt accessibility gate with compare-and-restore ownership.

The static instruction signature is adapted from wechatauto-replica at
d322c4a9b1124228820bab363e5676016f188fc6 (Apache-2.0). This implementation
does not run upstream code, try alternate addresses or change system settings.
See third_party/wechatauto-replica/LICENSE and NOTICE.md.
"""

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import re
import struct

from ..console_models import ConsoleError


VERSION = "4.1.15.56"
DLL_SHA256 = "932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99"
FLAG_RVA = 188336600
SIGNATURE = rb"\x48\x85\xc9\x0f\x84....\x80\x3d(?P<disp>.{4})\x00\x0f\x84"


@dataclass(frozen=True)
class Fingerprint:
    pid: int
    hwnd: int
    created: int
    version: str
    dll_sha256: str
    module_base: int
    module_size: int
    flag_rva: int


def verify_pe(path):
    """Validate the exact disk build and the one preselected writable target."""
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != DLL_SHA256:
        raise ConsoleError("patch_dll_fingerprint_mismatch")
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    if data[pe:pe+4] != b"PE\0\0" or struct.unpack_from("<H", data, pe+4)[0] != 0x8664:
        raise ConsoleError("patch_pe_invalid")
    count, optional_size = struct.unpack_from("<H", data, pe+6)[0], struct.unpack_from("<H", data, pe+20)[0]
    sections = []
    for i in range(count):
        at = pe+24+optional_size+i*40
        name = data[at:at+8].rstrip(b"\0")
        virtual_size, rva, raw_size, raw = struct.unpack_from("<IIII", data, at+8)
        flags = struct.unpack_from("<I", data, at+36)[0]
        sections.append((name, rva, virtual_size, raw, raw_size, flags))
    targets = []
    for name, rva, size, raw, raw_size, flags in sections:
        if name != b".text":
            continue
        for match in re.finditer(SIGNATURE, data[raw:raw+raw_size], re.DOTALL):
            # cmp byte ptr [rip+disp32], 0: displacement begins at offset 11.
            target = rva+match.start()+16+struct.unpack("<i", match["disp"])[0]
            if target == FLAG_RVA:
                targets.append((rva+match.start(), match.group()))
    writable = [s for s in sections if s[1] <= FLAG_RVA < s[1]+s[2] and s[5] & 0x80000000]
    if len(targets) != 1 or len(writable) != 1:
        raise ConsoleError("patch_static_target_mismatch")
    return targets[0]


class AccessibilityGate:
    """Native port holds a process HANDLE, never reopens a saved PID to write.

    Journal callbacks persist before mutation. A new process can recover a
    journal only after rechecking all fields against a freshly opened handle.
    No callback is allowed to approve a different fingerprint on recovery.
    """
    def __init__(self, native, *, allowed=False, save=lambda state: None):
        self.native, self.allowed, self.save = native, allowed, save
        self.record = None
        self.state = "disabled"

    def enable(self, expected, verify_controls):
        if not self.allowed:
            raise ConsoleError("accessibility_patch_not_enabled")
        if self.record:
            raise ConsoleError("patch_recovery_required")
        current = self.native.fingerprint()
        if current != expected:
            raise ConsoleError("patch_process_identity_changed")
        if (current.version != VERSION or current.dll_sha256 != DLL_SHA256
                or current.flag_rva != FLAG_RVA or not 0 <= FLAG_RVA < current.module_size):
            raise ConsoleError("patch_fingerprint_mismatch")
        self.native.validate_region()
        original = self.native.read()
        if original not in (0, 1):
            raise ConsoleError("patch_original_value_invalid")
        self.record = {"fingerprint": asdict(current), "original": original, "written": 1}
        self.save(self.record)  # Durable before WriteProcessMemory.
        self.state = "prepared"
        try:
            if original != 1:
                self.native.write(1)
            if self.native.read() != 1:
                raise ConsoleError("patch_readback_failed")
            if not verify_controls():
                raise ConsoleError("patch_controls_not_exposed")
            self.state = "enabled" if original == 0 else "already_enabled"
        except BaseException:
            self.restore()
            raise
        return self.status()

    def recover(self, record):
        self.record = record
        return self.restore()

    def restore(self):
        if not self.record:
            return self.status()
        record = self.record
        try:
            if not self.native.alive():
                self.state = "process_exited"
            elif asdict(self.native.fingerprint()) != record["fingerprint"]:
                # Never touch a reused PID / window. Preserve evidence for review.
                self.state = "restore_identity_mismatch"
                raise ConsoleError(self.state)
            elif record["original"] == 1:
                self.state = "unchanged"
            else:
                current = self.native.read()
                if current not in (record["original"], record["written"]):
                    raise ConsoleError("patch_restore_value_changed")
                if current == record["written"]:
                    self.native.validate_region()
                    self.native.write(record["original"])
                if self.native.read() != record["original"]:
                    raise ConsoleError("patch_restore_readback_failed")
                self.state = "restored"
            self.save(None)
            self.record = None
        except BaseException:
            if self.state != "restore_identity_mismatch":
                self.state = "restore_failed"
            raise
        return self.status()

    def status(self):
        return {"state": self.state, "recovery_required": self.record is not None,
                "fingerprint": self.record["fingerprint"] if self.record else None}


class NativeGate:
    """Minimal Win32 port: one data byte and one fixed instruction region only."""
    def __init__(self, hwnd):
        import ctypes as c
        from ctypes import wintypes as w
        if c.sizeof(c.c_void_p) != 8:
            raise ConsoleError("patch_requires_64bit_python")
        self.c, self.w, self.hwnd = c, w, int(hwnd)
        self.k, self.u = c.WinDLL("kernel32", use_last_error=True), c.WinDLL("user32", use_last_error=True)
        signatures = {
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "GetProcessTimes": ([w.HANDLE]+[c.POINTER(w.FILETIME)]*4, w.BOOL),
            "GetExitCodeProcess": ([w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
            "ReadProcessMemory": ([w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)], w.BOOL),
            "WriteProcessMemory": ([w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)], w.BOOL),
            "VirtualQueryEx": ([w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t], c.c_size_t),
        }
        for name, (args, result) in signatures.items():
            method = getattr(self.k, name); method.argtypes, method.restype = args, result
        self.u.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
        self.u.IsWindow.argtypes = [w.HWND]
        self.handle = None
        pid = w.DWORD()
        self.u.GetWindowThreadProcessId(self.hwnd, c.byref(pid))
        if not pid.value:
            raise ConsoleError("patch_window_missing")
        self.pid = pid.value
        self.handle = self.k.OpenProcess(0x1000 | 0x0400 | 0x0010 | 0x0020 | 0x0008, False, self.pid)
        if not self.handle:
            raise ConsoleError("patch_process_access_denied")
        try:
            import win32api
            import win32process
            modules = win32process.EnumProcessModules(self.handle)
            matches = [(module, win32process.GetModuleFileNameEx(self.handle, module)) for module in modules]
            matches = [(module, path) for module, path in matches if Path(path).name.lower() == "weixin.dll"]
            if len(matches) != 1:
                raise ConsoleError("patch_module_ambiguous")
            self.base, path = matches[0]
            self.path = Path(path)
            # SizeOfImage from the PE optional header, checked against live allocation below.
            with self.path.open("rb") as source:
                header = source.read(4096)
            pe = struct.unpack_from("<I", header, 0x3c)[0]
            self.size = struct.unpack_from("<I", header, pe+24+56)[0]
            info = win32api.GetFileVersionInfo(str(self.path), "\\")
            ms, ls = info["FileVersionMS"], info["FileVersionLS"]
            self.version = f"{ms>>16}.{ms&65535}.{ls>>16}.{ls&65535}"
            if self.version != VERSION:
                raise ConsoleError("patch_version_mismatch")
            self.instruction_rva, self.instruction = verify_pe(self.path)
            self.created = self._created()
            self.validate_region()
        except BaseException:
            self.close()
            raise

    def _created(self):
        values = [self.w.FILETIME() for _ in range(4)]
        if not self.k.GetProcessTimes(self.handle, *(self.c.byref(v) for v in values)):
            raise ConsoleError("patch_process_times_unavailable")
        return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime

    def fingerprint(self):
        pid = self.w.DWORD()
        self.u.GetWindowThreadProcessId(self.hwnd, self.c.byref(pid))
        if pid.value != self.pid or not self.alive():
            raise ConsoleError("patch_process_identity_changed")
        return Fingerprint(self.pid, self.hwnd, self._created(), self.version, DLL_SHA256,
                           self.base, self.size, FLAG_RVA)

    def alive(self):
        code = self.w.DWORD()
        if not self.k.GetExitCodeProcess(self.handle, self.c.byref(code)):
            raise ConsoleError("patch_process_state_unavailable")
        return code.value == 259

    def _read_at(self, address, count):
        buffer, read = self.c.create_string_buffer(count), self.c.c_size_t()
        if not self.k.ReadProcessMemory(self.handle, address, buffer, count, self.c.byref(read)) or read.value != count:
            raise ConsoleError("patch_memory_read_failed")
        return buffer.raw

    def validate_region(self):
        c, w = self.c, self.w
        class MBI(c.Structure):
            _fields_ = [("BaseAddress", c.c_void_p), ("AllocationBase", c.c_void_p),
                        ("AllocationProtect", w.DWORD), ("PartitionId", w.WORD),
                        ("RegionSize", c.c_size_t), ("State", w.DWORD), ("Protect", w.DWORD), ("Type", w.DWORD)]
        address = self.base+FLAG_RVA
        region = MBI()
        if (not self.k.VirtualQueryEx(self.handle, address, c.byref(region), c.sizeof(region))
                or region.AllocationBase != self.base or region.State != 0x1000
                or region.Type != 0x1000000 or region.Protect not in (0x04, 0x08)
                or not self.base <= address < self.base+self.size):
            raise ConsoleError("patch_memory_region_mismatch")
        if self._read_at(self.base+self.instruction_rva, len(self.instruction)) != self.instruction:
            raise ConsoleError("patch_loaded_instruction_mismatch")

    def read(self):
        return self._read_at(self.base+FLAG_RVA, 1)[0]

    def write(self, value):
        self.fingerprint()
        self.validate_region()
        byte, written = self.c.c_ubyte(value), self.c.c_size_t()
        if (not self.k.WriteProcessMemory(self.handle, self.base+FLAG_RVA, self.c.byref(byte), 1, self.c.byref(written))
                or written.value != 1):
            raise ConsoleError("patch_memory_write_failed")

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None
