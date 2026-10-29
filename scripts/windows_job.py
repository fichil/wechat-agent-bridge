"""Kill-on-close job containing only processes started by this launcher."""
import ctypes as C
from ctypes import wintypes as W


class IO_COUNTERS(C.Structure):
    _fields_ = [(name, C.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class BASIC_LIMITS(C.Structure):
    _fields_ = [("PerProcessUserTimeLimit", C.c_longlong), ("PerJobUserTimeLimit", C.c_longlong),
                ("LimitFlags", W.DWORD), ("MinimumWorkingSetSize", C.c_size_t),
                ("MaximumWorkingSetSize", C.c_size_t), ("ActiveProcessLimit", W.DWORD),
                ("Affinity", C.c_size_t), ("PriorityClass", W.DWORD), ("SchedulingClass", W.DWORD)]


class EXTENDED_LIMITS(C.Structure):
    _fields_ = [("BasicLimitInformation", BASIC_LIMITS), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", C.c_size_t), ("JobMemoryLimit", C.c_size_t),
                ("PeakProcessMemoryUsed", C.c_size_t), ("PeakJobMemoryUsed", C.c_size_t)]


class WindowsJob:
    def __init__(self):
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [C.c_void_p, W.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = W.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        limits = EXTENDED_LIMITS()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, C.byref(limits), C.sizeof(limits)):
            self.close()
            raise RuntimeError("Cannot configure owned process cleanup")

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise RuntimeError("Cannot attach owned process to cleanup job")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
