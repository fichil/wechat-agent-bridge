"""One bounded hidden worker. No UI input, focus, window or clipboard operations.

Remote-call/module enumeration adapted from helper; see native_send/LICENSE.helper.
When a target thread may still be running, its memory and DLL are deliberately retained.
"""
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import re
import struct
import sys
import time
from uuid import uuid4

from .compatibility import BUILD, DLL, CORE_HASHES, digest, build_identity, accepted
from wechat_account_probe.native import creation_time, handle_sid, process_sid

K = c.WinDLL("kernel32", use_last_error=True)
P = c.c_void_p
for name, args, result in (
    ("OpenProcess", [w.DWORD,w.BOOL,w.DWORD],w.HANDLE),
    ("CloseHandle", [w.HANDLE],w.BOOL),
    ("CreateToolhelp32Snapshot", [w.DWORD,w.DWORD],w.HANDLE),
    ("GetModuleHandleW", [w.LPCWSTR],w.HMODULE),
    ("GetProcAddress", [w.HMODULE,c.c_char_p],P),
    ("GetModuleHandleExW", [w.DWORD,P,c.POINTER(w.HMODULE)],w.BOOL),
    ("GetModuleFileNameW", [w.HMODULE,w.LPWSTR,w.DWORD],w.DWORD),
    ("VirtualAllocEx", [w.HANDLE,P,c.c_size_t,w.DWORD,w.DWORD],P),
    ("VirtualFreeEx", [w.HANDLE,P,c.c_size_t,w.DWORD],w.BOOL),
    ("WriteProcessMemory", [w.HANDLE,P,P,c.c_size_t,c.POINTER(c.c_size_t)],w.BOOL),
    ("ReadProcessMemory", [w.HANDLE,P,P,c.c_size_t,c.POINTER(c.c_size_t)],w.BOOL),
    ("CreateRemoteThread", [w.HANDLE,P,c.c_size_t,P,P,w.DWORD,c.POINTER(w.DWORD)],w.HANDLE),
    ("WaitForSingleObject", [w.HANDLE,w.DWORD],w.DWORD),
    ("GetExitCodeThread", [w.HANDLE,c.POINTER(w.DWORD)],w.BOOL),
    ("GetProcessTimes", [w.HANDLE]+[c.POINTER(w.FILETIME)]*4,w.BOOL),
    ("CreateMutexW", [P,w.BOOL,w.LPCWSTR],w.HANDLE),
    ("ReleaseMutex", [w.HANDLE],w.BOOL),
):
    function = getattr(K,name); function.argtypes=args; function.restype=result


class Module(c.Structure):
    _fields_=[("size",w.DWORD),("module_id",w.DWORD),("pid",w.DWORD),
              ("global_usage",w.DWORD),("usage",w.DWORD),("base",P),
              ("image_size",w.DWORD),("module",w.HMODULE),("name",w.WCHAR*256),("path",w.WCHAR*260)]


class Config(c.Structure):
    _fields_=[("size",w.DWORD),("pid",w.DWORD),("created",c.c_uint64),
              ("log_path",w.WCHAR*260),("text",c.c_char*1024),("account",c.c_char*128),
              ("token",c.c_char*40),("target",c.c_char*128)]


for name in ("Module32FirstW","Module32NextW"):
    getattr(K,name).argtypes=[w.HANDLE,c.POINTER(Module)]


def check(value):
    if not value: raise OSError("native_operation_failed")
    return value


def modules(pid):
    handle=K.CreateToolhelp32Snapshot(0x18,pid)
    if handle==c.c_void_p(-1).value: raise OSError("source_offline")
    try:
        item=Module(); item.size=c.sizeof(item)
        check(K.Module32FirstW(handle,c.byref(item))); found={}
        while True:
            found[item.name.lower()]=(item.base,item.path)
            if not K.Module32NextW(handle,c.byref(item)): break
        return found
    finally: K.CloseHandle(handle)


def system_function(pid,name):
    address=check(K.GetProcAddress(K.GetModuleHandleW("kernel32.dll"),name))
    owner=w.HMODULE(); check(K.GetModuleHandleExW(6,address,c.byref(owner)))
    path=c.create_unicode_buffer(32768); check(K.GetModuleFileNameW(owner,path,len(path)))
    return modules(pid)[Path(path.value).name.lower()][0]+address-owner.value


def pe_exports(path):
    """Parse file exports without loading code into the Python process."""
    data=Path(path).read_bytes(); pe=struct.unpack_from('<I',data,60)[0]
    count=struct.unpack_from('<H',data,pe+6)[0]; optional=struct.unpack_from('<H',data,pe+20)[0]
    sections=[struct.unpack_from('<8sIIII',data,pe+24+optional+i*40) for i in range(count)]
    def offset(rva):
        for _,vs,va,rs,rp in sections:
            if va<=rva<va+max(vs,rs): return rp+rva-va
        raise ValueError('invalid_pe')
    export=offset(struct.unpack_from('<I',data,pe+24+112)[0])
    _,_,_,_,_,_,_,names,functions,addresses,ordinals=struct.unpack_from('<IIHHIIIIIII',data,export)
    result={}
    for i in range(names):
        namepos=offset(struct.unpack_from('<I',data,offset(addresses)+4*i)[0])
        name=data[namepos:data.index(b'\0',namepos)].decode('ascii')
        ordinal=struct.unpack_from('<H',data,offset(ordinals)+2*i)[0]
        result[name]=struct.unpack_from('<I',data,offset(functions)+4*ordinal)[0]
    return result


def code_windows(path):
    """Code entry signatures for all borrowed native calls, read from the verified file."""
    with Path(path).open('rb') as stream:
        head=stream.read(4096); pe=struct.unpack_from('<I',head,60)[0]
        count=struct.unpack_from('<H',head,pe+6)[0]; optional=struct.unpack_from('<H',head,pe+20)[0]
        sections=[struct.unpack_from('<8sIIII',head,pe+24+optional+i*40) for i in range(count)]
        result={}
        for rva in (0x45d10,0x469a0,0x47310,0x30f450,0x3855c0,0x55f0380,0x754bcdc,0x754bd20):
            for _,vs,va,rs,rp in sections:
                if va<=rva<va+max(vs,rs):
                    stream.seek(rp+rva-va);result[rva]=stream.read(32);break
            if rva not in result: raise ValueError('native_version_unsupported')
        return result


def inspect(request):
    pid=int(request['pid']); created=int(request['created']); account=request['account']
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,127}',account): raise ValueError('account_invalid')
    process=check(K.OpenProcess(0x410,False,pid))
    try:
        if creation_time(K,process)!=created or handle_sid(K,process)!=process_sid(K,os.getpid()):
            raise ValueError('source_identity_changed')
        loaded_modules=modules(pid)
        if DLL.name.lower() in loaded_modules: raise ValueError('native_busy')
        core=loaded_modules.get('weixin.dll')
        if not core: raise ValueError('source_offline')
        core_hash=digest(core[1])
        if Path(core[1]).stat().st_size!=204305968 or core_hash not in CORE_HASHES:
            raise ValueError('native_version_unsupported')
        def read(address,size):
            data=c.create_string_buffer(size); n=c.c_size_t()
            check(K.ReadProcessMemory(process,address,data,size,c.byref(n)))
            if n.value!=size: raise ValueError('identity_unavailable')
            return data.raw
        def ptr(address): return struct.unpack('<Q',read(address,8))[0]
        base=core[0]
        for rva,signature in code_windows(core[1]).items():
            if read(base+rva,len(signature))!=signature: raise ValueError('native_code_changed')
        app=ptr(base+0xbb92fa0); context=ptr(app+0x68)
        if ptr(app)!=base+0x8f514e8 or ptr(context)!=base+0x8fd1468:
            raise ValueError('identity_unavailable')
        if ptr(base+0x8f514e8+0x60)!=base+0x469a0 or ptr(base+0x8fd1468+0x18)!=base+0x3855c0:
            raise ValueError('identity_unavailable')
        if read(base+0x3855c0,5)!=b'\x48\x8d\x41\x48\xc3': raise ValueError('identity_unavailable')
        header=read(context+0x48,32); length,capacity=struct.unpack_from('<QQ',header,16)
        if not 1<=length<=127 or capacity<length or not (capacity==15 or 16<=capacity<=1048576):
            raise ValueError('identity_unavailable')
        address=context+0x48 if capacity==15 else struct.unpack_from('<Q',header)[0]
        body=read(address,length)
        if (body.decode('ascii')!=account or read(context+0x48,32)!=header or read(address,length)!=body
                or ptr(base+0xbb92fa0)!=app or ptr(app+0x68)!=context):
            raise ValueError('source_identity_changed')
        dll_hash=build_identity()
        return dict(core_sha256=core_hash,dll_sha256=dll_hash,ready=accepted(account,core_hash,dll_hash))
    finally: K.CloseHandle(process)


def send(request):
    status='failed'; submitted=False; pending=False; process=None; allocations=[]; mutex=None; owned=False
    loaded=None; exports={}; cleanup=False; module_owned=False
    token=uuid4().hex; report=BUILD/('receipt-'+token+'.json')
    def call(address,parameter):
        nonlocal pending
        thread=check(K.CreateRemoteThread(process,None,0,address,parameter,0,None))
        pending=True
        try:
            if K.WaitForSingleObject(thread,30000)!=0: raise TimeoutError('native_timeout')
            pending=False; code=w.DWORD(); check(K.GetExitCodeThread(thread,c.byref(code)))
            return code.value
        finally: K.CloseHandle(thread)
    def allocate(data):
        address=check(K.VirtualAllocEx(process,None,len(data),0x3000,4)); allocations.append(address)
        n=c.c_size_t(); buffer=c.create_string_buffer(data)
        check(K.WriteProcessMemory(process,address,buffer,len(data),c.byref(n)))
        if n.value!=len(data): raise OSError('short_write')
        return address
    try:
        identity=inspect(request)
        target=request['target']; text=request['text']; encoded=text.encode('utf-8')
        if not re.fullmatch(r'[A-Za-z0-9_.@-]{1,127}',target) or not 0<len(encoded)<=1023 or b'\0' in encoded:
            raise ValueError('native_text_invalid')
        testing=request.get('acceptance_test') is True
        if testing and (target!='filehelper' or not text.startswith('BRIDGE_TEST_')):
            raise ValueError('acceptance_target_invalid')
        if not identity['ready'] and not testing: raise ValueError('native_acceptance_required')
        pid=int(request['pid']); created=int(request['created'])
        mutex=check(K.CreateMutexW(None,False,f'Local\\WechatAgentSend-{pid}-{created}'))
        owned=K.WaitForSingleObject(mutex,0) in (0,128)
        if not owned: raise ValueError('native_busy')
        process=check(K.OpenProcess(0x43a,False,pid))
        if creation_time(K,process)!=created: raise ValueError('source_identity_changed')
        exports=pe_exports(DLL)
        loaded=modules(pid).get(DLL.name.lower())
        if loaded:
            # A previous timed-out worker owns this module. Never call possibly stale exports.
            loaded=None
            raise ValueError('native_busy')
        if not loaded:
            call(system_function(pid,b'LoadLibraryW'),allocate((str(DLL)+'\0').encode('utf-16-le')))
            loaded=modules(pid).get(DLL.name.lower())
            if not loaded: raise ValueError('native_load_failed')
            module_owned=True
        if call(loaded[0]+exports['WxWaitForTasks'],0)!=0: raise ValueError('native_busy')
        if call(loaded[0]+exports['WxInspectCoreVersion'],0)!=2: raise ValueError('native_version_unsupported')
        config=Config(); config.size=c.sizeof(config); config.pid=pid; config.created=created
        config.log_path=str(report); config.text=encoded; config.target=target.encode('ascii')
        config.account=request['account'].encode('ascii'); config.token=token.encode('ascii')
        parameter=allocate(bytes(config))
        # From this point a lost response is ambiguous, never a retryable failure.
        submitted=True
        code=call(loaded[0]+exports['WxSendText'],parameter)
        if code in (4,21,87,170):
            status='failed'
        else:
            status='unknown'; deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                try:
                    receipt=json.loads(report.read_text(encoding='utf-8'))
                    if receipt.get('token')==token and receipt.get('status') in ('sent','failed','unknown'):
                        status=receipt['status']; break
                except (OSError,ValueError): pass
                time.sleep(.05)
        return dict(status=status,code='native_completed' if status=='sent' else 'native_result_uncertain' if status=='unknown' else 'native_rejected',**identity)
    except Exception as exc:
        return dict(status='unknown' if submitted else 'failed',code='native_result_uncertain' if submitted else
                    str(exc) if isinstance(exc,ValueError) else 'native_unavailable')
    finally:
        if process and not pending:
            try:
                if loaded and module_owned:
                    deadline=time.monotonic()+5
                    while time.monotonic()<deadline:
                        if call(loaded[0]+exports['WxWaitForTasks'],0)==0:
                            call(system_function(int(request['pid']),b'FreeLibrary'),loaded[0]); cleanup=True; break
                        time.sleep(.1)
            except Exception: pass
            if not pending:
                for address in allocations: K.VirtualFreeEx(process,address,0,0x8000)
        if process: K.CloseHandle(process)
        if owned: K.ReleaseMutex(mutex)
        if mutex: K.CloseHandle(mutex)
        if cleanup:
            try: report.unlink(missing_ok=True)
            except OSError: pass


if __name__=='__main__':
    try:
        request=json.loads(sys.stdin.buffer.readline(65536))
        result=inspect(request) if request.get('operation')=='inspect' else send(request)
    except Exception as exc:
        result=dict(ready=False,status='failed',code=str(exc) if isinstance(exc,ValueError) else 'native_unavailable')
    print(json.dumps(result,ensure_ascii=True),flush=True)
