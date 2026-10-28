"""Explicit, read-only in-thread inspection of the already authorized test UI.

Requires same-build synthetic recovery evidence. No Qt method, draft, send,
keyboard, clipboard or focus operation is available in this candidate build.
"""
import argparse
import ctypes as c
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import probe_background_draft as original
from scripts.test_thread_hook import Contract, CASES, validate_build, harness_sources, call, U32, U64, I32, PTR
from scripts.build_thread_hook import BOUND_OUT,SEND_OUT
from wechat_agent_bridge.console_models import ConsoleError


class BoundInspection(c.Structure):
    _fields_=[(x,U64) for x in ('magic','nonce','module_base','module_size')]+[
        ('version',U32),('bytes',U32),('uid',U32*3),('last_input',U32),('widget',U64*3),
        ('foreground',U64),('clipboard',U32),('mode',U32)]+[
        (x,I32) for x in ('checked','selected','gui_thread','reason','operation_started','operation_returned','exception_code')]


def require_recovery(path,*,bound_send=False):
    path=Path(path).resolve()
    if path.parent!=original.DIRECTORY.resolve() or path.stat().st_size>200000:
        raise ConsoleError('bound_hook_local_evidence_required')
    report=json.loads(path.read_text(encoding='utf-8-sig'))
    if (report.get('passed') is not True or report.get('synthetic_only') is not True or
        report.get('guard_fixture',{}).get('passed') is not True or
        report.get('harness_unchanged') is not True or report.get('harness_sources')!=harness_sources() or
        report.get('build')!=validate_build(bound_inspector=True,bound_send=bound_send) or
        len(report.get('cases',[]))!=len(CASES) or {x.get('case') for x in report['cases']}!=set(CASES) or
        any(x.get('passed') is not True or x.get('focus_changed') is not False for x in report['cases'])):
        raise ConsoleError('bound_hook_same_build_recovery_required')
    return report['build']


def module_present(native):
    import win32process
    # Exact bound process handle, module metadata only, no module contents.
    for module in win32process.EnumProcessModules(native.handle):
        path=Path(win32process.GetModuleFileNameEx(native.handle,module))
        if path.name.lower()=='bridge_thread_hook.dll':
            return True
    return False


class BoundProbe:
    def __init__(self,fp,native,checkpoint,*,send_candidate=False):
        self.fp,self.native,self.checkpoint=fp,native,checkpoint
        self.send_candidate=send_candidate
        self.directory=SEND_OUT if send_candidate else BOUND_OUT
        self.k=c.WinDLL('kernel32',use_last_error=True); self.u=c.WinDLL('user32',use_last_error=True)
        self.maps=[]; self.s=self.b=self.owner=None
        self.journal_owned=False
        self.report=dict(cleanup={},qt_method_called=False,send_invoked=False)
        from wechat_agent_bridge.console_store import ConsoleStore
        from wechat_agent_bridge.protection import DPAPIProtector
        self.store=ConsoleStore(original.DIRECTORY/'gate.sqlite3',DPAPIProtector())

    def mapping(self,name,kind):
        handle=call(self.k,'CreateFileMappingW',PTR,[PTR,PTR,U32,U32,U32,c.c_wchar_p])(
            PTR(-1),None,4,0,c.sizeof(kind),name)
        if not handle or c.get_last_error()==183:
            if handle: call(self.k,'CloseHandle',I32,[PTR])(handle)
            raise ConsoleError('bound_hook_mapping_creation_failed')
        self.maps.append([handle,None])
        view=call(self.k,'MapViewOfFile',PTR,[PTR,U32,U32,U32,c.c_size_t])(handle,0xF001F,0,0,c.sizeof(kind))
        if not view: raise ConsoleError('bound_hook_mapping_failed')
        self.maps[-1][1]=view
        return kind.from_address(view)

    def wait(self,predicate,seconds=3):
        until=time.monotonic()+seconds; next_check=0
        while not predicate():
            if time.monotonic()>=until: raise ConsoleError('bound_hook_timeout')
            if time.monotonic()>=next_check:
                self.checkpoint(); next_check=time.monotonic()+.1
            time.sleep(.01)
        self.checkpoint()

    def run(self,runtimes,widgets,*,emit=False,before_dispatch=None):
        if emit and not self.send_candidate: raise ConsoleError('bound_hook_readonly_build_required')
        if self.store.get('thread_hook','pending') or module_present(self.native):
            raise ConsoleError('bound_hook_prior_recovery_required')
        build=validate_build(bound_inspector=True,bound_send=self.send_candidate)
        nonce=int.from_bytes(os.urandom(8),'little') or 1
        self.name=f'Local\\BridgeM1ThreadHook-{os.getpid()}-{nonce:016x}'
        self.s=self.mapping(self.name,Contract); s=self.s
        self.b=self.mapping(self.name+'-Bound',BoundInspection); b=self.b
        times=(U64(),U64(),U64(),U64())
        if not call(self.k,'GetProcessTimes',I32,[PTR,PTR,PTR,PTR,PTR])(PTR(-1),*(c.byref(x) for x in times)):
            raise ConsoleError('bound_hook_controller_identity_failed')
        pid=U32()
        tid=call(self.u,'GetWindowThreadProcessId',U32,[PTR,c.POINTER(U32)])(self.fp.hwnd,c.byref(pid))
        if not tid or pid.value!=self.fp.pid: raise ConsoleError('bound_hook_gui_identity_mismatch')
        s.magic,s.version,s.bytes=0x4252494447454831,1,c.sizeof(Contract)
        s.controller_pid,s.controller_created,s.nonce=os.getpid(),times[0].value,nonce
        s.target_pid,s.target_tid,s.target_created,s.hwnd=self.fp.pid,tid,self.fp.created,self.fp.hwnd
        b.magic,b.version,b.bytes,b.nonce=0x4252494447454931,1,c.sizeof(BoundInspection),nonce
        b.module_base,b.module_size=self.fp.module_base,self.fp.module_size
        b.mode=int(emit)
        b.uid[:]=[x[-1]&0xffffffff for x in runtimes]; b.widget[:]=widgets
        class LastInput(c.Structure): _fields_=[('size',U32),('ticks',U32)]
        info=LastInput(c.sizeof(LastInput),0)
        if not call(self.u,'GetLastInputInfo',I32,[PTR])(c.byref(info)): raise ConsoleError('bound_hook_input_state_unavailable')
        b.last_input=info.ticks
        b.foreground=call(self.u,'GetForegroundWindow',PTR,[])() or 0
        b.clipboard=call(self.u,'GetClipboardSequenceNumber',U32,[])()
        # No SwitchDesktop. Windows Task View desktops share this Win32 desktop.
        desktop=call(self.u,'GetThreadDesktop',PTR,[U32])(call(self.k,'GetCurrentThreadId',U32,[])())
        name=c.create_unicode_buffer(256); needed=U32()
        if not call(self.u,'GetUserObjectInformationW',I32,[PTR,I32,PTR,U32,c.POINTER(U32)])(desktop,2,name,c.sizeof(name),c.byref(needed)):
            raise ConsoleError('bound_hook_desktop_identity_unavailable')
        self.desktop=name.value
        self.store.put('thread_hook','pending',dict(fingerprint=asdict(self.fp),controller_pid=os.getpid(),
            controller_created=s.controller_created,mode='bound_send' if emit else 'readonly_inspection',build=build))
        self.journal_owned=True
        self.owner=subprocess.Popen([str(self.directory/'bridge_hook_synthetic.exe'),'owner',self.name,self.desktop,
            str(self.directory/'bridge_thread_hook.dll')],creationflags=0x08000000,
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.wait(lambda:s.owner_ready==1 or self.owner.poll() is not None)
        if self.owner.poll() is not None or s.owner_pid!=self.owner.pid:
            raise ConsoleError('bound_hook_owner_not_ready')
        self.report['owner_installed']=True
        if before_dispatch: before_dispatch()
        s.request=1; s.deadline=call(self.k,'GetTickCount64',U64,[])()+1500; s.phase=1; s.wake=1
        self.wait(lambda:s.wake_done==1 and s.phase in (3,4))
        self.report.update(status=s.status,phase=s.phase,entries=s.entries,transport_delivered=s.wake_result==1,
            metadata_checked=b.checked==1,selected=b.selected==1,gui_thread_match=b.gui_thread==tid,guard_reason=b.reason)
        self.report.update(qt_method_called=b.operation_started==1,method_returned=b.operation_returned==1,
                           exception_code=b.exception_code,send_invoked=b.operation_started==1)
        if not(s.status==1 and b.checked==1 and b.selected==1 and b.gui_thread==tid and s.wake_result==1):
            raise ConsoleError('bound_hook_guard_rejected')
        if emit and not (b.operation_started==1 and b.operation_returned==1 and b.exception_code==0):
            raise ConsoleError('bound_hook_dispatch_result_unknown')

    def close(self):
        errors=[]
        if self.s: self.s.stop=self.s.owner_stop=1
        if self.owner:
            try: self.owner.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # Only our held Popen process, never the client.
                self.owner.terminate(); self.owner.wait(timeout=2); errors.append('owner_forced_exit')
            self.report['cleanup'].update(owner_exitcode=self.owner.returncode,
                unhooked=self.s.owner_unhooked,drained=self.s.owner_drained)
            if self.owner.returncode or self.s.owner_unhooked!=1 or self.s.owner_drained!=1:
                errors.append('owner_cleanup_unconfirmed')
            if not errors:
                entries=self.s.entries
                probe=subprocess.run([str(self.directory/'bridge_hook_synthetic.exe'),'probe',self.name,self.desktop,
                    str(self.directory/'bridge_thread_hook.dll')],creationflags=0x08000000,
                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=2)
                self.report['cleanup']['post_unhook_delivered']=probe.returncode==0
                self.report['cleanup']['post_unhook_no_callback']=self.s.entries==entries
                if probe.returncode or self.s.entries!=entries: errors.append('unhook_probe_failed')
        until=time.monotonic()+2
        while module_present(self.native) and time.monotonic()<until: time.sleep(.02)
        unloaded=not module_present(self.native)
        self.report['cleanup']['helper_unloaded']=unloaded
        if not unloaded: errors.append('module_unload_unconfirmed')
        if self.b:
            self.report.update(qt_method_called=self.b.operation_started==1,method_returned=self.b.operation_returned==1,
                exception_code=self.b.exception_code,send_invoked=self.b.operation_started==1,guard_reason=self.b.reason)
        self.s=self.b=None
        for handle,view in reversed(self.maps):
            if view and not call(self.k,'UnmapViewOfFile',I32,[PTR])(view): errors.append('unmap_failed')
            if not call(self.k,'CloseHandle',I32,[PTR])(handle): errors.append('mapping_close_failed')
        self.report['cleanup']['mapping_handles_closed']=not any(x in errors for x in ('unmap_failed','mapping_close_failed'))
        if not errors and self.journal_owned: self.store.put('thread_hook','pending',None)
        self.store.close()
        self.report['cleanup']['errors']=errors
        if errors: raise ConsoleError('bound_hook_recovery_unconfirmed')


def worker(pipe,fp_dict,names,expected,write,*,send_candidate=False):
    sys.coinit_flags=0
    native=reader=probe=None; result=None; failure=None
    try:
        if write: raise ConsoleError('bound_hook_write_forbidden')
        from scripts.uia_client_bootstrap import prepare_client_elements,read_pattern_context,require_safe_context
        from scripts.probe_bound_uia_context import prepare_send_element,check_send_element
        from scripts.probe_send_accessible import inspect_interface,inspect_session_guard
        from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
        from wechat_agent_bridge.adapters.accessibility_gate import NativeGate,Fingerprint
        from wechat_agent_bridge.adapters.runtime_accessibility import ExactProfile,ReadOnlyProcess,BoundedMemory,interface_for_id
        prepared=prepare_client_elements(fp_dict,expected); send=prepare_send_element(prepared,fp_dict)
        backend=WindowsUIA(); current=backend.target_control_probe(fp_dict['pid'],fp_dict['hwnd'],names)
        original._exchange(pipe,'verified',dict(target=current,root_runtime_id=list(prepared[1].GetRuntimeId())))
        original.check_draft_target(expected,current)
        require_safe_context(read_pattern_context(prepared[0]._client,prepared[2],prepared[3]))
        fp=Fingerprint(**fp_dict); native=NativeGate(fp.hwnd)
        if native.fingerprint()!=fp: raise ConsoleError('bound_hook_identity_changed')
        profile=ExactProfile(native.path.read_bytes()); reader=ReadOnlyProcess(fp,native.fingerprint)
        memory=BoundedMemory(reader.read,reader.check,max_bytes=32768)
        sys.path.insert(0,str(ROOT/'.local/static-tools'))
        inspect_interface(memory,fp,profile,list(send[3]),inspect_connections=True)
        session=inspect_session_guard(memory,fp,profile,expected['session_runtime_id'])
        if session.get('selected_state_layout_verified') is not True: raise ConsoleError('bound_hook_selected_layout_unverified')
        runtimes=[expected['input_runtime_id'],list(send[3]),expected['session_runtime_id']]
        widgets=[]
        for runtime in runtimes:
            iface=interface_for_id(memory,fp.module_base,runtime)
            weak=memory.number(iface+8); control=memory.number(weak)
            if not control or memory.number(control+4,'<i')==0: raise ConsoleError('bound_hook_widget_destroyed')
            widgets.append(memory.number(weak+8))
        memory.recheck()
        def checkpoint():
            original._exchange(pipe,'checkpoint','readonly_bound_gui_thread')
        original.check_draft_target(expected,backend.target_control_probe(fp.pid,fp.hwnd,names))
        checkpoint()
        probe=BoundProbe(fp,native,checkpoint,send_candidate=send_candidate)
        probe.run(runtimes,widgets)
        check_send_element(send,fp_dict,prepared[-1])
        original.check_draft_target(expected,backend.target_control_probe(fp.pid,fp.hwnd,names))
        checkpoint()
        result=dict(state='preflight_verified',attempted=False,inspection=probe.report,fingerprint=asdict(fp),runtimes=runtimes)
    except Exception as exc:
        failure=str(exc) if isinstance(exc,ConsoleError) else 'bound_hook_worker_failed_'+type(exc).__name__
    finally:
        if probe:
            try: probe.close()
            except Exception: failure='bound_hook_recovery_unconfirmed'
        if reader: reader.close()
        if native: native.close()
        try:
            if failure: pipe.send(dict(kind='error',value=failure,details=probe.report if probe else None))
            else: pipe.send(dict(kind='result',value=result))
        finally: pipe.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--inspect-bound',action='store_true')
    parser.add_argument('--allow-accessibility-patch',action='store_true')
    parser.add_argument('--allow-process-helper',action='store_true')
    parser.add_argument('--send-candidate',action='store_true',help='Inspect the send-capable build in mode zero; still never dispatch a signal.')
    parser.add_argument('--current-desktop-background',action='store_true')
    parser.add_argument('--target-kind',choices=('group','private'),default='group')
    parser.add_argument('--private-label')
    for flag in ('account','group','private','target-evidence','synthetic-evidence','virtual-desktop','hook-evidence'):
        parser.add_argument('--'+flag)
    args=parser.parse_args(argv)
    if not args.inspect_bound:
        print(json.dumps(dict(state='disabled',send_enabled=False))); return 0
    args.run,args.preflight,args.operator_idle=False,True,False
    source=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(); build=None
    try:
        from scripts.probe_bound_uia_context import require_context_canary
        if not args.allow_process_helper: raise ConsoleError('bound_hook_explicit_helper_opt_in_required')
        if not args.virtual_desktop or not args.synthetic_evidence or not args.hook_evidence:
            raise ConsoleError('bound_hook_evidence_and_desktop_required')
        require_context_canary(args.synthetic_evidence); build=require_recovery(args.hook_evidence,bound_send=args.send_candidate)
        from functools import partial
        scope=None
        if args.target_kind=='private':
            from scripts.private_test_scope import PrivateTestScope
            scope=PrivateTestScope(args.private,args.private_label)
        report=original.run(args,taskview_desktop_id=args.virtual_desktop,
            inspection_worker=partial(worker,send_candidate=args.send_candidate),current_desktop=args.current_desktop_background,
            target_scope=scope)
        if validate_build(bound_inspector=True,bound_send=args.send_candidate)!=build: raise ConsoleError('bound_hook_build_changed')
    except Exception as exc:
        report=dict(state='failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'bound_hook_preparation_failed')
    report.update(kind='bound_gui_thread_readonly_inspection',build=build,inspector_sha256=source,
                  send_invoked=False,api_called=False,qt_method_called=False)
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=source:
        report.update(state='failed',reason='bound_hook_inspector_changed')
    path=original.DIRECTORY/('bound-thread-hook-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as stream: json.dump(report,stream,indent=2)
    print(json.dumps(dict(path=str(path),state=report['state'],reason=report.get('reason'),patch_restored=report.get('patch_restored'))))
    return 0 if report['state']=='preflight_verified' else 1


if __name__=='__main__': raise SystemExit(main())
