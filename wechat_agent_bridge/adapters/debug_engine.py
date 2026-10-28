"""Small x64 DbgEng diagnostic port. No injection, software breakpoint or write API.

ABI declarations follow Microsoft's documented IDebug* interfaces. The Windows
SDK header and debugger binaries are not redistributed. Native methods are used
only by the explicit research coordinator, never the console send path.
"""
import ctypes as c
import os
from pathlib import Path
import time
import uuid
import threading
from functools import wraps

from ..console_models import ConsoleError

U32, U64, PTR = c.c_uint32, c.c_uint64, c.c_void_p


def owner_thread(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        if getattr(self, 'owner_thread_id', None) != threading.get_ident():
            raise ConsoleError('dbgeng_wrong_owner_thread')
        return method(self, *args, **kwargs)
    return invoke


class StackFrame(c.Structure):
    _fields_ = [("instruction", U64), ("return_address", U64), ("frame", U64),
                ("stack", U64), ("function_table", U64), ("parameters", U64*4),
                ("reserved", U64*6), ("virtual", c.c_int32), ("number", U32)]


def _guid(text):
    return (c.c_ubyte*16).from_buffer_copy(uuid.UUID(text).bytes_le)


def _call(pointer, index, types=(), values=(), *, allow_false=False):
    table = c.cast(pointer, c.POINTER(c.POINTER(PTR))).contents
    fn = c.WINFUNCTYPE(c.c_int32, PTR, *types)(table[index])
    hr = fn(pointer, *values)
    if hr < 0 or (hr != 0 and not allow_false):
        raise ConsoleError(f"dbgeng_call_{index}_failed_{hr & 0xffffffff:08x}")
    return hr


class _Events:
    """COM callback storage; all functions remain alive until engine release."""
    def __init__(self):
        self.owner_thread_id = threading.get_ident()
        self.kind = None
        self.exception = None
        self.sequence = 0
        self.pending = None
        self.continuation_sequence = None
        self.functions = []
        signatures = [(PTR, PTR), (), (), (PTR,), (PTR,), (PTR, U32),
                      (U64, U64, U64), (U32,),
                      (U64, U64, U64, U32, PTR, PTR, U32, U32, U64, U64, U64),
                      (U32,), (U64, U64, U32, PTR, PTR, U32, U32), (PTR, U64),
                      (U32, U32), (U32,), (U32, U64), (U32, U64), (U32, U64)]
        for index, signature in enumerate(signatures):
            def receive(this, *args, index=index):
                if index == 0:
                    wanted = c.string_at(args[0], 16)
                    accepted = (uuid.UUID("00000000-0000-0000-c000-000000000046").bytes_le,
                                uuid.UUID("337be28b-5036-4d72-b6bf-c45fbb9f2eaa").bytes_le)
                    c.cast(args[1], c.POINTER(PTR))[0] = self.pointer if wanted in accepted else None
                    return 0 if wanted in accepted else -2147467262
                if index in (1, 2): return 1
                if index == 3:
                    c.cast(args[0], c.POINTER(U32))[0] = 3  # breakpoint + exception
                if index == 4:
                    self.record("breakpoint")
                    return 6
                if index == 5:
                    self.record("exception", c.cast(args[0], c.POINTER(U32))[0])
                    return 6
                return 0
            self.functions.append(c.WINFUNCTYPE(c.c_int32, PTR, *signature)(receive))
        self.table = (PTR*len(self.functions))(*(c.cast(fn, PTR).value for fn in self.functions))
        self.object = (PTR*1)(c.cast(self.table, PTR).value)
        self.pointer = c.cast(self.object, PTR).value

    def record(self, kind, exception=None):
        self.sequence += 1
        self.kind, self.exception = kind, exception
        self.pending = {"sequence": self.sequence, "kind": kind, "exception": exception}
        self.continuation_sequence = None

    def request_continuation(self, sequence):
        if sequence is not None and self.pending and self.pending["sequence"] == sequence:
            self.continuation_sequence = sequence

    def complete_continuation(self, sequence):
        # WaitForEvent can deliver a new stop while continuing the old one.
        # Never retire that new event using the previous continuation token.
        if sequence is not None and self.pending and self.pending["sequence"] == sequence:
            self.clear_pending()

    def clear_pending(self):
        self.pending = None
        self.kind = self.exception = None
        self.continuation_sequence = None


class DebugEngine:
    def __init__(self):
        self.owner_thread_id = threading.get_ident()
        if os.name != "nt" or c.sizeof(PTR) != 8:
            raise ConsoleError("dbgeng_requires_windows_x64")
        self.interfaces, self.breakpoints = [], []
        self.attached = False
        self.prepared = False
        self.user_target_attached = False
        self.bootstrap_pid = None
        self.dll = c.WinDLL(str(Path(os.environ["SystemRoot"])/"System32/dbgeng.dll"), winmode=0x800)
        self.dll.DebugCreate.argtypes = [PTR, c.POINTER(PTR)]
        self.dll.DebugCreate.restype = c.c_int32
        self.client = PTR()
        iid = _guid("27fe5639-8407-4f47-8364-ee118fb08ac8")
        if self.dll.DebugCreate(c.byref(iid), c.byref(self.client)) != 0:
            raise ConsoleError("dbgeng_create_failed")
        self.interfaces.append(self.client)
        try:
            self.control = self._query("5182e668-105e-416e-ad92-24ef800424ba")
            self.registers = self._query("ce289126-9e84-45a7-937e-67bb18691493")
            self.system = self._query("6b86fe2c-2c4f-4f0c-9da2-174311acc327")
            self.events = _Events()
            _call(self.client, 46, (PTR,), (self.events.pointer,))
            _call(self.control, 54, (U32,), (0x20,))  # DEBUG_ENGOPT_INITIAL_BREAK
        except BaseException:
            self.close()
            raise

    @owner_thread
    def _query(self, name):
        iid, pointer = _guid(name), PTR()
        _call(self.client, 0, (PTR, c.POINTER(PTR)), (c.byref(iid), c.byref(pointer)))
        self.interfaces.append(pointer)
        return pointer

    @owner_thread
    def attach(self, pid):
        if not self.prepared:
            raise ConsoleError("dbgeng_bootstrap_required")
        if self.user_target_attached:
            raise ConsoleError("dbgeng_second_user_target_forbidden")
        _call(self.client, 12, (U64, U32, U32), (0, pid, 0))
        self.user_target_attached = True
        self.attached = True

    @owner_thread
    def prepare(self, bootstrap_pid):
        """Establish detach-on-exit using our own child BEFORE a real target.

        DbgEng rejects process options before its first debugging connection.
        Keep that connection alive so its global exit policy covers subsequent
        targets; never open this initial unsafe window against a user's process.
        """
        if self.prepared or self.bootstrap_pid:
            raise ConsoleError("dbgeng_already_prepared")
        self.bootstrap_pid = bootstrap_pid
        _call(self.client, 12, (U64, U32, U32), (0, bootstrap_pid, 0))
        self.attached = True
        deadline = time.monotonic()+5
        while not self.wait(100):
            if time.monotonic() >= deadline:
                raise ConsoleError("dbgeng_bootstrap_timeout")
        _call(self.client, 18, (U32,), (3,))
        flags = U32()
        _call(self.client, 15, (c.POINTER(U32),), (c.byref(flags),))
        if flags.value & 3 != 3:
            raise ConsoleError("dbgeng_detach_on_exit_unavailable")
        self.prepared = True
        self.resume()
        self.wait(100)

    @owner_thread
    def wait(self, milliseconds=100):
        if not 0 <= milliseconds <= 1000:
            raise ValueError("dbgeng_wait_not_bounded")
        previous = self.events.continuation_sequence
        result = _call(self.control, 93, (U32, U32), (0, milliseconds), allow_false=True)
        self.events.complete_continuation(previous)
        return result == 0

    @owner_thread
    def _set_execution_status(self, status):
        previous = self.events.pending['sequence'] if self.events.pending else None
        _call(self.control, 50, (U32,), (status,))
        # DbgEng applies the requested execution status while WaitForEvent runs.
        # Preserve the obligation if that wait fails, rather than claiming the
        # event was consumed merely because SetExecutionStatus returned.
        self.events.request_continuation(previous)

    @owner_thread
    def resume(self):
        self._set_execution_status(1)  # DEBUG_STATUS_GO

    @owner_thread
    def interrupt(self):
        _call(self.control, 4, (U32,), (0,))

    @owner_thread
    def add_execution_breakpoint(self, address):
        if len(self.breakpoints) >= 4:
            raise ConsoleError("dbgeng_hardware_slots_exhausted")
        pointer = PTR()
        _call(self.control, 72, (U32, U32, c.POINTER(PTR)), (1, 0xffffffff, c.byref(pointer)))
        # DATA + EXECUTE means processor breakpoint, not software INT3.
        self.breakpoints.append(pointer)
        _call(pointer, 11, (U64,), (address,))
        _call(pointer, 13, (U32, U32), (1, 4))
        _call(pointer, 7, (U32,), (4,))

    @owner_thread
    def confirm_exit_policy(self):
        _call(self.client, 18, (U32,), (3,))
        flags = U32()
        _call(self.client, 15, (c.POINTER(U32),), (c.byref(flags),))
        if flags.value & 3 != 3:
            raise ConsoleError("dbgeng_detach_on_exit_unavailable")

    @owner_thread
    def register(self, name):
        if name not in ("rip", "rcx", "rdx", "r8", "r9", "rax", "rdi"):
            raise ValueError("register_not_allowlisted")
        index = U32()
        _call(self.registers, 5, (c.c_char_p, c.POINTER(U32)), (name.encode("ascii"), c.byref(index)))
        value = (U64*4)()  # DEBUG_VALUE: 24-byte union, tail ULONG, type ULONG.
        _call(self.registers, 6, (U32, PTR), (index.value, c.byref(value)))
        return value[0]

    @owner_thread
    def thread_id(self):
        result = U32()
        _call(self.system, 17, (c.POINTER(U32),), (c.byref(result),))
        return result.value

    @owner_thread
    def process_id(self):
        result = U32()
        _call(self.system, 27, (c.POINTER(U32),), (c.byref(result),))
        return result.value

    @owner_thread
    def stack(self):
        frames, count = (StackFrame*64)(), U32()
        _call(self.control, 31, (U64, U64, U64, PTR, U32, c.POINTER(U32)),
              (0, 0, 0, c.byref(frames), 64, c.byref(count)))
        # Deliberately exclude stack parameters: they can include private data.
        return [frames[i].instruction for i in range(min(count.value, 64))]

    @owner_thread
    def clear_breakpoints(self):
        # Remove only after native success, so a partial failure cannot retry
        # an already removed interface on a second cleanup attempt.
        while self.breakpoints:
            _call(self.control, 73, (PTR,), (self.breakpoints[0],))
            self.breakpoints.pop(0)

    @owner_thread
    def detach(self):
        if self.attached:
            self.clear_breakpoints()
            # Consume a pending breakpoint before detach. Otherwise single-step
            # may be delivered to the application as an unhandled exception.
            pending = self.events.pending
            if pending and pending['kind'] in ('breakpoint','exception'):
                # Only DbgEng's breakpoint callback identifies an owned stop.
                # An arbitrary application exception must not be swallowed.
                if self.events.continuation_sequence != pending['sequence']:
                    status = 2 if pending['kind'] == 'breakpoint' else 3
                    self._set_execution_status(status)
                self.wait(100)
            _call(self.client, 25)
            self.attached = False
            self.events.clear_pending()

    @owner_thread
    def close(self):
        # If detach fails, preserve the interfaces for a bounded cleanup retry.
        # Releasing the last client while still attached is not recovery proof.
        self.detach()
        for pointer in reversed(self.interfaces):
            table = c.cast(pointer, c.POINTER(c.POINTER(PTR))).contents
            c.WINFUNCTYPE(U32, PTR)(table[2])(pointer)
        self.interfaces.clear()
