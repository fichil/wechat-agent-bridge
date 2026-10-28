"""Synthetic-child-only Win32 control for comparison with DbgEng.

No CLI, no client binding, no production import. Called only by the native test
parent with the PID and TID of the child it just created. No memory writes.
"""
import ctypes as c
import os
import struct
import time


def debugger(connection, pid, address, mode, bootstrap_pid, tid):
    k = c.WinDLL('kernel32', use_last_error=True)
    k.DebugActiveProcess.argtypes = k.DebugActiveProcessStop.argtypes = [c.c_uint32]
    k.DebugSetProcessKillOnExit.argtypes = [c.c_int]
    k.WaitForDebugEvent.argtypes = [c.c_void_p,c.c_uint32]
    k.ContinueDebugEvent.argtypes = [c.c_uint32,c.c_uint32,c.c_uint32]
    k.OpenThread.argtypes, k.OpenThread.restype = [c.c_uint32,c.c_int,c.c_uint32], c.c_void_p
    k.CloseHandle.argtypes = [c.c_void_p]
    k.GetThreadContext.argtypes = k.SetThreadContext.argtypes = [c.c_void_p,c.c_void_p]
    def checked(ok):
        if not ok: raise RuntimeError('win32_control_error_'+str(c.get_last_error()))
    thread = None
    attached = False
    original = None
    pending = None
    def context():
        storage = c.create_string_buffer(1247)
        pointer = (c.addressof(storage)+15)&~15
        c.c_uint32.from_address(pointer+48).value = 0x100010
        checked(k.GetThreadContext(thread,pointer))
        return storage,pointer
    def restore():
        if original is not None:
            storage,pointer=context()
            c.memmove(pointer+72,original,48)
            checked(k.SetThreadContext(thread,pointer))
    try:
        checked(k.DebugActiveProcess(pid)); attached=True
        checked(k.DebugSetProcessKillOnExit(False))
        thread=k.OpenThread(0x0008|0x0010,False,tid)
        checked(thread)
        event=(c.c_uint64*22)()
        until=time.monotonic()+5
        armed=False
        while time.monotonic()<until:
            if not k.WaitForDebugEvent(c.byref(event),100):
                if c.get_last_error()==121: continue
                checked(False)
            raw=bytes(event)
            kind,event_pid,event_tid=struct.unpack_from('<III',raw)
            if event_pid!=pid: raise RuntimeError('win32_event_target_mismatch')
            pending=(event_pid,event_tid,0x10002)
            if kind in (3,6):
                handle=struct.unpack_from('<Q',raw,16)[0]
                if handle: k.CloseHandle(handle)
            if kind==1:
                code=struct.unpack_from('<I',raw,16)[0]
                pending=(event_pid,event_tid,0x80010001)
                if code==0x80000003 and not armed:
                    storage,pointer=context()
                    original=c.string_at(pointer+72,48)
                    c.c_uint64.from_address(pointer+72).value=address
                    c.c_uint64.from_address(pointer+112).value=1
                    checked(k.SetThreadContext(thread,pointer)); armed=True
                    pending=(event_pid,event_tid,0x10002)
                elif code==0x80000004 and event_tid==tid and armed:
                    connection.send({'breakpoint_hit':True,'stack_frames':0,'native_control':True})
                    if mode=='crash': os._exit(17)
                    if mode=='watchdog':
                        connection.send({'awaiting_external_termination':True})
                        while True: time.sleep(1)
                    restore(); original=None
                    pending=(event_pid,event_tid,0x10002)
                    checked(k.ContinueDebugEvent(*pending)); pending=None
                    checked(k.DebugActiveProcessStop(pid)); attached=False
                    connection.send({'detached':True})
                    return
            checked(k.ContinueDebugEvent(*pending)); pending=None
        raise RuntimeError('win32_control_timeout')
    except Exception as exc:
        connection.send({'error':str(exc)})
    finally:
        if attached:
            try:
                if pending:
                    restore()
                    checked(k.ContinueDebugEvent(*pending))
                checked(k.DebugActiveProcessStop(pid))
            except Exception as exc: connection.send({'cleanup_error':str(exc)})
        if thread: k.CloseHandle(thread)
