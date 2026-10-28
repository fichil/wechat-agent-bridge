# ABI reference

Source: https://github.com/winsiderss/phnt/tree/53fbbdc5b5d2b08761db1c7b26bfa8c820924356

Reviewed files: `ntpsapi.h`, `ntdbg.h`, and the MIT `LICENSE` at that exact commit.
The original license is retained in this directory.

`scripts/probe_debug_object_retention.py` and `scripts/probe_debug_object_recovery.py`
independently declare the small ctypes ABI needed for ProcessDebugObjectHandle,
NtDebugContinue, and NtRemoveProcessDebug. No upstream implementation, examples,
headers, or binaries are installed, run, or bundled. Only owned synthetic child
processes are supported by these experimental entrypoints. No production or
stable Windows API compatibility is claimed.

`scripts/probe_debug_object_delivery.py` additionally declares the x64
DBGUI_WAIT_STATE_CHANGE layout and NtCreateDebugObject, NtDebugActiveProcess,
and NtWaitForDebugEvent signatures from the same fixed ABI reference. The
implementation is independent and only supports its own synthetic children.
