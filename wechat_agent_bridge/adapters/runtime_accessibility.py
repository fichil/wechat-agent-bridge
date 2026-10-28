"""Bounded metadata-only snapshots for the exact researched x64 build.

This is not a chat-reading or writing adapter. No heap scanning, remote calls,
text-buffer reads, process writes or debugger attachment are implemented here.
"""
import bisect
import hashlib
import struct

from .accessibility_gate import DLL_SHA256, VERSION
from .pe_inventory import PE
from .static_types import qt_inheritance
from ..console_models import ConsoleError

CACHE_RVA = 0xBBB13D0
ACCESSIBLE_TABLE = 0x908F258
SETTER_RVA = 0x9D5990


class ExactProfile:
    def __init__(self, data):
        if hashlib.sha256(data).hexdigest() != DLL_SHA256:
            raise ConsoleError("runtime_profile_fingerprint_mismatch")
        self.pe = PE(data)
        self.base = 0x180000000
        rva, size = self.pe.unpack('<II', self.pe.directories+24)
        self.functions = [self.pe.unpack('<III', self.pe.offset(rva+i, 12))[:2] for i in range(0, size, 12)]
        self.starts = [a for a, _ in self.functions]
        # Independently checked file instructions. Never search another build.
        self.anchors = {0x51A730: bytes.fromhex('8b4110c3'),
                        0x1B8F0F5: bytes.fromhex('488b05d422020a'),
                        0x1B8F200: bytes.fromhex('4c8b49104183791400'),
                        0x1B8FF00: bytes.fromhex('488b4108488b084885c9'),
                        0x1E96E0D: bytes.fromhex('488b8e88020000'),
                        0x2A1633: bytes.fromhex('488b4e40'),
                        0x2A1720: bytes.fromhex('4d8b6528'),
                        0x2A1AF0: bytes.fromhex('4d8b6d10'),
                        0x1A2E0B: bytes.fromhex('486342184901c04c89c148ff6210')}
        for rva, value in self.anchors.items():
            if data[self.pe.offset(rva, len(value)):self.pe.offset(rva)+len(value)] != value:
                raise ConsoleError("runtime_profile_anchor_mismatch")

    def validate_loaded(self, memory, fp):
        if fp.version != VERSION or fp.dll_sha256 != DLL_SHA256:
            raise ConsoleError("runtime_profile_fingerprint_mismatch")
        for rva, expected in self.anchors.items():
            if memory.read(fp.module_base+rva, len(expected)) != expected:
                raise ConsoleError("runtime_loaded_anchor_mismatch")

    def metadata_for_table(self, table_rva):
        """Decode only a verified metaObject getter's static return, not RTTI text.

        This client obfuscates RTTI names. A dynamic/unknown getter stays unknown.
        Capstone is a research-only dependency, loaded by the diagnostic script.
        """
        from capstone import Cs, CS_ARCH_X86, CS_MODE_64, __version__
        if __version__ != "5.0.6":
            raise ConsoleError("research_tool_version_mismatch")
        try:
            target = self.pe.unpack('<Q', self.pe.offset(table_rva, 8))[0]-self.base
            index = bisect.bisect_right(self.starts, target)-1
            if index >= 0 and self.functions[index][0] == target:
                _, end = self.functions[index]
            else:
                end = target+64  # leaf getter must terminate in this small span
            if end-target > 256:
                return None
            candidates = []
            previous = None
            for ins in Cs(CS_ARCH_X86, CS_MODE_64).disasm(self.pe.data[self.pe.offset(target):self.pe.offset(end-1)+1], target):
                if ins.mnemonic == 'ret':
                    if previous and previous.bytes[:3] == b'\x48\x8d\x05' and previous.size == 7:
                        meta = previous.address+7+struct.unpack('<i', previous.bytes[3:])[0]
                        candidates.append(qt_inheritance(self.pe, self.base, meta))
                    # Compiled getters can have an earlier dynamic branch return.
                    if end == target+64: break
                previous = ins
            return candidates[0] if len(candidates) == 1 else None
        except (ValueError, UnicodeError, struct.error):
            return None


class BoundedMemory:
    def __init__(self, read, identity_check, *, max_bytes=262144):
        self.raw_read, self.identity_check = read, identity_check
        self.remaining = max_bytes
        self.witnesses = {}

    def read(self, address, count, *, witness=False):
        if not 0x10000 <= address < 0x800000000000 or not 0 < count <= 4096 or count > self.remaining:
            raise ConsoleError("runtime_memory_bounds")
        self.identity_check()
        result = self.raw_read(address, count)
        self.remaining -= count
        if len(result) != count:
            raise ConsoleError("runtime_memory_short_read")
        if witness:
            old = self.witnesses.setdefault((address, count), result)
            if old != result:
                raise ConsoleError("runtime_object_changed")
        return result

    def number(self, address, fmt='<Q', *, witness=True):
        return struct.unpack(fmt, self.read(address, struct.calcsize(fmt), witness=witness))[0]

    def recheck(self):
        for (address, count), expected in list(self.witnesses.items()):
            if self.read(address, count) != expected:
                raise ConsoleError("runtime_object_changed")


def interface_for_id(memory, base, runtime_id):
    if (not isinstance(runtime_id, (list, tuple)) or not 2 <= len(runtime_id) <= 16
            or any(type(x) is not int or not -0x80000000 <= x <= 0xffffffff for x in runtime_id)):
        raise ConsoleError("runtime_id_invalid")
    uid = runtime_id[-1] & 0xffffffff
    if not 0x80000000 <= uid < 0xffffffff:
        raise ConsoleError("runtime_id_not_qt_accessible_id")
    cache = memory.number(base+CACHE_RVA)
    table = memory.number(cache+0x10)
    size = memory.number(table+0x14, '<I')
    buckets = memory.number(table+0x20, '<I')
    seed = memory.number(table+0x24, '<I')
    if not 0 < size <= 1000000 or not 0 < buckets <= 1000000:
        raise ConsoleError("runtime_cache_bounds")
    hashed = uid ^ seed
    array = memory.number(table+8)
    node = memory.number(array+8*(hashed % buckets))
    seen = set()
    while node != table:
        if node in seen or len(seen) >= 128:
            raise ConsoleError("runtime_cache_cycle_or_limit")
        seen.add(node)
        if memory.number(node+8, '<I') == hashed and memory.number(node+12, '<I') == uid:
            return memory.number(node+16)
        node = memory.number(node)
    raise ConsoleError("runtime_id_stale")


def object_metadata(memory, base, size, profile, obj):
    table = memory.number(obj)
    if not base <= table < base+size:
        raise ConsoleError("runtime_vtable_outside_module")
    hierarchy = profile.metadata_for_table(table-base)
    return {"vtable_rva": table-base, "hierarchy": hierarchy,
            "class": hierarchy[0]["class"] if hierarchy else "unresolved"}


def connections(memory, obj, base, size, profile, *, related=None, slot_inspector=None):
    private = memory.number(obj+8)
    data = memory.number(private+0x40)
    if not data: return []
    vector = memory.number(data+8)
    if not vector: return []
    count = memory.number(vector+8, '<I')
    if count > 256: raise ConsoleError("runtime_signal_vector_limit")
    records = []
    # -1 is Qt's all-signals list. Headers and nodes only, never argument data.
    for signal in range(-1, count):
        node = memory.number(vector+0x20+16*signal)
        seen = set()
        while node:
            if node in seen or len(records) >= 128:
                raise ConsoleError("runtime_connection_cycle_or_limit")
            seen.add(node)
            sender = memory.number(node+0x20)
            if sender != obj: raise ConsoleError("runtime_connection_sender_mismatch")
            receiver = memory.number(node+0x28)
            flags = memory.number(node+0x58, '<H')
            kind = flags & 7
            if kind > 3: raise ConsoleError("runtime_connection_type_invalid")
            callback = memory.number(node+0x38)
            member_target, receiver_adjustment = None, None
            slot_details = None
            if flags & 8 and callback:
                slot_object = callback
                callback = memory.number(slot_object+8)  # QSlotObjectBase impl
                if callback == base+0x1A2DF0:
                    member = memory.number(slot_object+0x10)
                    receiver_adjustment = memory.number(slot_object+0x18, '<i')
                    if base <= member < base+size:
                        member_target = member-base
                if slot_inspector is not None:
                    slot_details = slot_inspector(slot_object, callback, receiver)
            callback_rva = callback-base if base <= callback < base+size else None
            metadata = object_metadata(memory, base, size, profile, receiver) if receiver else None
            if related is not None and metadata and metadata["class"] == "mmui::ChatInputView":
                related[receiver] = metadata
            records.append({"signal_index": signal, "connection_type": kind,
                            "receiver_vtable_rva": metadata["vtable_rva"] if metadata else None,
                            "receiver_class": metadata["class"] if metadata else "disconnected",
                            "callback_rva": callback_rva, "slot_object": bool(flags & 8),
                            "member_target_rva": member_target, "receiver_adjustment": receiver_adjustment,
                            "method_offset": None if flags & 8 else memory.number(node+0x50, '<H'),
                            "method_relative": None if flags & 8 else memory.number(node+0x52, '<H')})
            if slot_details is not None:
                records[-1]['slot_details'] = slot_details
            node = memory.number(node+0x10)
    return records


def inspect_binding(memory, fp, profile, runtime_id):
    profile.validate_loaded(memory, fp)
    base, size = fp.module_base, fp.module_size
    iface = interface_for_id(memory, base, runtime_id)
    if memory.number(iface) != base+ACCESSIBLE_TABLE or memory.number(base+ACCESSIBLE_TABLE+0x60) != base+SETTER_RVA:
        raise ConsoleError("runtime_accessible_type_mismatch")
    weak = memory.number(iface+8)
    control = memory.number(weak)
    if not control or memory.number(control+4, '<i') == 0:
        raise ConsoleError("runtime_object_destroyed")
    widget = memory.number(weak+8)
    metadata = object_metadata(memory, base, size, profile, widget)
    if metadata["class"] != "mmui::ChatInputField":
        raise ConsoleError("runtime_input_class_unverified")
    private = memory.number(widget+8)
    text_control = memory.number(private+0x288)
    text_metadata = object_metadata(memory, base, size, profile, text_control)
    if text_metadata["class"] != "QWidgetTextControl":
        raise ConsoleError("runtime_text_control_class_unverified")
    related = {}
    input_connections = connections(memory, widget, base, size, profile, related=related)
    text_connections = connections(memory, text_control, base, size, profile)
    if len(related) > 1:
        raise ConsoleError("runtime_input_view_ambiguous")
    views = [{"metadata": meta, "connections": connections(memory, obj, base, size, profile)}
             for obj, meta in related.items()]
    report = {"runtime_binding": "observed_metadata_snapshot", "write_permission": False,
              "accessible_vtable_rva": ACCESSIBLE_TABLE, "setter_rva": SETTER_RVA,
              "input": metadata, "text_control": text_metadata,
              "input_connections": input_connections, "text_connections": text_connections,
              "related_input_views": views,
              "causality": "not_observed", "limitation": "Non-atomic snapshot; no receiver execution or queued delivery proven."}
    memory.recheck()
    return report


class ReadOnlyProcess:
    """Separate least-privilege handle; no WriteProcessMemory API on this port."""
    def __init__(self, fp, identity):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w, self.fp, self.identity = c, w, fp, identity
        self.k = c.WinDLL("kernel32", use_last_error=True)
        self.k.OpenProcess.argtypes, self.k.OpenProcess.restype = [w.DWORD,w.BOOL,w.DWORD], w.HANDLE
        self.k.CloseHandle.argtypes = [w.HANDLE]
        self.k.ReadProcessMemory.argtypes = [w.HANDLE,c.c_void_p,c.c_void_p,c.c_size_t,c.POINTER(c.c_size_t)]
        self.k.VirtualQueryEx.argtypes = [w.HANDLE,c.c_void_p,c.c_void_p,c.c_size_t]
        self.k.VirtualQueryEx.restype = c.c_size_t
        self.handle = self.k.OpenProcess(0x0400|0x10, False, fp.pid)
        if not self.handle: raise ConsoleError("runtime_read_access_denied")

    def check(self):
        if self.identity() != self.fp: raise ConsoleError("runtime_process_identity_changed")

    def read(self, address, count):
        c, w = self.c, self.w
        self.check()
        class MBI(c.Structure):
            _fields_ = [("base",c.c_void_p),("allocation",c.c_void_p),("allocation_protect",w.DWORD),
                        ("partition",w.WORD),("size",c.c_size_t),("state",w.DWORD),("protect",w.DWORD),("kind",w.DWORD)]
        region = MBI()
        if (not self.k.VirtualQueryEx(self.handle,address,c.byref(region),c.sizeof(region))
                or region.state != 0x1000 or region.protect & 0x101
                or not region.base <= address < address+count <= region.base+region.size):
            raise ConsoleError("runtime_region_unreadable")
        buffer, used = c.create_string_buffer(count), c.c_size_t()
        if not self.k.ReadProcessMemory(self.handle,address,buffer,count,c.byref(used)) or used.value != count:
            raise ConsoleError("runtime_memory_read_failed")
        return buffer.raw

    def close(self):
        if self.handle: self.k.CloseHandle(self.handle); self.handle = None
