"""Explicit native restore probe on one owned hidden window/private desktop.

Default is disabled. No WeChat, UIA, debugger, model or downloaded executable.
"""
import argparse
import ctypes as c
import json
import multiprocessing as mp
import os
import platform
from pathlib import Path
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.test_focus_etw import call,PTR,U32,desktop_name,input_snapshot
from scripts.development_window_restore import WindowPort,restore_transaction,synthetic_sources
from wechat_agent_bridge.adapters.background import snapshot,unchanged


def child_main(pipe,name):
    user=c.WinDLL('user32',use_last_error=True);kernel=c.WinDLL('kernel32')
    desktop=window=original=None;cleanup={};phase='thread_identity'
    try:
        tid=call(kernel,'GetCurrentThreadId',U32,[])()
        original=call(user,'GetThreadDesktop',PTR,[U32])(tid)
        phase='open_private_desktop'
        desktop=call(user,'OpenDesktopW',PTR,[c.c_wchar_p,U32,c.c_int,U32])(name,0,False,0x83)
        if not desktop:raise RuntimeError('synthetic_desktop_open_failed')
        phase='attach_private_desktop'
        if not call(user,'SetThreadDesktop',c.c_int,[PTR])(desktop):raise RuntimeError('synthetic_desktop_attach_failed')
        phase='verify_private_desktop'
        if desktop_name(user,desktop)!=name or input_snapshot()[0]==name:raise RuntimeError('synthetic_desktop_not_isolated')
        phase='create_window'
        window=call(user,'CreateWindowExW',PTR,[U32,c.c_wchar_p,c.c_wchar_p,U32,c.c_int,c.c_int,c.c_int,c.c_int,PTR,PTR,PTR,PTR])(
            0,'STATIC','Owned restore fixture',0xcf0000,0,0,240,120,None,None,None,None)
        if not window:raise RuntimeError('synthetic_window_create_failed')
        focus=call(user,'GetFocus',PTR,[])()
        pipe.send(dict(ready=True,pid=os.getpid(),hwnd=window))
        phase='message_pump'
        peek=call(user,'PeekMessageW',c.c_int,[PTR,PTR,U32,U32,U32])
        translate=call(user,'TranslateMessage',c.c_int,[PTR]);dispatch=call(user,'DispatchMessageW',c.c_ssize_t,[PTR])
        message=c.create_string_buffer(48);until=time.monotonic()+25
        while time.monotonic()<until:
            while peek(message,None,0,0,1):translate(message);dispatch(message)
            if pipe.poll(.01):
                if pipe.recv()!='finish':raise RuntimeError('synthetic_restore_protocol_mismatch')
                pipe.send(dict(minimized=WindowPort(window).state()==dict(visible=True,iconic=True),
                    normal_visible=WindowPort(window).state()==dict(visible=True,iconic=False),
                    local_focus_unchanged=call(user,'GetFocus',PTR,[])()==focus))
                break
        else:raise RuntimeError('synthetic_child_deadline')
    except BaseException as exc:
        try:pipe.send(dict(error=str(exc) if isinstance(exc,RuntimeError) else 'synthetic_restore_child_failed',
            phase=phase,winerror=c.get_last_error()))
        except (OSError,EOFError):pass
    finally:
        if window:cleanup['window_destroyed']=bool(call(user,'DestroyWindow',c.c_int,[PTR])(window))
        if original:cleanup['thread_desktop_restored']=bool(call(user,'SetThreadDesktop',c.c_int,[PTR])(original))
        if desktop:cleanup['child_desktop_closed']=bool(call(user,'CloseDesktop',c.c_int,[PTR])(desktop))
        try:pipe.send(dict(cleanup=cleanup))
        except (OSError,EOFError):pass
        pipe.close()


def requester_main(pipe,name,hwnd,expected_pid):
    user=c.WinDLL('user32',use_last_error=True);kernel=c.WinDLL('kernel32')
    desktop=original=None;cleanup={}
    try:
        tid=call(kernel,'GetCurrentThreadId',U32,[])()
        original=call(user,'GetThreadDesktop',PTR,[U32])(tid)
        desktop=call(user,'OpenDesktopW',PTR,[c.c_wchar_p,U32,c.c_int,U32])(name,0,False,0x83)
        if not desktop or not call(user,'SetThreadDesktop',c.c_int,[PTR])(desktop):
            raise RuntimeError('synthetic_requester_desktop_attach_failed')
        if desktop_name(user,desktop)!=name or input_snapshot()[0]==name:
            raise RuntimeError('synthetic_requester_not_isolated')
        focus=call(user,'GetFocus',PTR,[])();requests=[]
        def check():
            owner=U32();thread=call(user,'GetWindowThreadProcessId',U32,[PTR,c.POINTER(U32)])(hwnd,c.byref(owner))
            if not thread or owner.value!=expected_pid or expected_pid==os.getpid():
                raise RuntimeError('synthetic_requester_window_owner_mismatch')
            if call(user,'GetFocus',PTR,[])()!=focus:raise RuntimeError('synthetic_requester_focus_changed')
        check();port=WindowPort(hwnd)
        result=restore_transaction(port,check,lambda value:requests.append(value),allowed=True)
        normal_requests=[]
        normal_result=restore_transaction(WindowPort(hwnd,normal=True),check,
            lambda value:normal_requests.append(value),allowed=True,normal=True)
        pipe.send(dict(result=result,request_count=len(requests),requester_owner_match=True,
            normal_result=normal_result,normal_request_count=len(normal_requests),
            requester_distinct_process=True,requester_focus_unchanged=call(user,'GetFocus',PTR,[])()==focus))
    except BaseException as exc:
        try:pipe.send(dict(error=str(exc) if isinstance(exc,RuntimeError) else 'synthetic_requester_failed',winerror=c.get_last_error()))
        except (OSError,EOFError):pass
    finally:
        if original:cleanup['requester_thread_desktop_restored']=bool(call(user,'SetThreadDesktop',c.c_int,[PTR])(original))
        if desktop:cleanup['requester_desktop_closed']=bool(call(user,'CloseDesktop',c.c_int,[PTR])(desktop))
        try:pipe.send(dict(cleanup=cleanup))
        except (OSError,EOFError):pass
        pipe.close()


def run():
    user=c.WinDLL('user32',use_last_error=True)
    report=dict(kind='synthetic_window_restore',contains_real_client=False,passed=False,sources=synthetic_sources(),
        os=platform.version(),cleanup={'terminated':False})
    desktop=process=parent=child=requester=request_parent=request_child=None;checks=[];name='CodexRestore-'+uuid.uuid4().hex
    before=input_snapshot();phase='create_desktop'
    try:
        desktop=call(user,'CreateDesktopW',PTR,[c.c_wchar_p,c.c_wchar_p,PTR,U32,U32,PTR])(name,None,None,0,0x1ff,None)
        if not desktop:raise RuntimeError('synthetic_desktop_create_failed')
        phase='spawn'
        context=mp.get_context('spawn');parent,child=context.Pipe();process=context.Process(target=child_main,args=(child,name))
        process.start();child.close()
        phase='ready'
        if not parent.poll(10):raise RuntimeError('synthetic_ready_timeout')
        ready=parent.recv()
        if ready.get('error'):report['child_error']=ready
        if ready.get('ready') is not True or ready.get('pid')!=process.pid:raise RuntimeError('synthetic_owner_mismatch')
        phase='initial_state'
        baseline=snapshot(process.pid)
        def check():
            unchanged(baseline,snapshot(process.pid))
            if input_snapshot()!=before:raise RuntimeError('synthetic_working_desktop_changed')
            checks.append(True)
        phase='transaction'
        request_parent,request_child=context.Pipe()
        requester=context.Process(target=requester_main,args=(request_child,name,ready['hwnd'],process.pid))
        requester.start();request_child.close();until=time.monotonic()+15
        while not request_parent.poll(.02):
            check()
            if time.monotonic()>=until or not requester.is_alive():raise RuntimeError('synthetic_requester_timeout_or_exit')
        requested=request_parent.recv();check()
        if requested.get('error'):report['requester_error']=requested;raise RuntimeError('synthetic_requester_failed')
        report.update(requested)
        if not request_parent.poll(5):raise RuntimeError('synthetic_requester_cleanup_timeout')
        report['cleanup'].update(request_parent.recv().get('cleanup',{}))
        parent.send('finish')
        phase='final_state'
        if not parent.poll(5):raise RuntimeError('synthetic_final_state_timeout')
        final=parent.recv()
        if not final.get('normal_visible'):raise RuntimeError('synthetic_native_normal_state_not_verified')
        report['normal_visibility_verified']=True
        report['local_focus_unchanged']=final.get('local_focus_unchanged') is True
        if not parent.poll(5):raise RuntimeError('synthetic_cleanup_timeout')
        report['cleanup'].update(parent.recv().get('cleanup',{}))
    except BaseException as exc:
        report['reason']=str(exc) if isinstance(exc,RuntimeError) else 'synthetic_window_restore_failed'
        report['phase']=phase;report['winerror']=c.get_last_error()
    finally:
        if parent:
            try:parent.send('finish')
            except (OSError,EOFError):pass
        if process and process.pid:
            process.join(3)
            if process.is_alive():process.terminate();process.join(2);report['cleanup']['terminated']=True
            report['cleanup'].update(child_exited=not process.is_alive(),child_exitcode=process.exitcode)
            if not process.is_alive():process.close()
        if requester and requester.pid:
            requester.join(3)
            if requester.is_alive():requester.terminate();requester.join(2);report['cleanup']['terminated']=True
            report['cleanup'].update(requester_exited=not requester.is_alive(),requester_exitcode=requester.exitcode)
            if not requester.is_alive():requester.close()
        if parent:
            try:
                for _ in range(4):
                    if not parent.poll(.01):break
                    extra=parent.recv()
                    if isinstance(extra.get('cleanup'),dict):report['cleanup'].update(extra['cleanup'])
                    if extra.get('error'):report['child_error']=extra
            except (OSError,EOFError):pass
        for pipe in (parent,child,request_parent,request_child):
            if pipe:pipe.close()
        if desktop:report['cleanup']['parent_desktop_closed']=bool(call(user,'CloseDesktop',c.c_int,[PTR])(desktop))
        remaining=call(user,'OpenDesktopW',PTR,[c.c_wchar_p,U32,c.c_int,U32])(name,0,False,1)
        report['cleanup']['desktop_absent']=not remaining and c.get_last_error()==2
        if remaining:call(user,'CloseDesktop',c.c_int,[PTR])(remaining)
        report['working_desktop_unchanged']=input_snapshot()==before
        report['checkpoint_count']=len(checks)
        report['sources_unchanged']=synthetic_sources()==report['sources']
        required=('child_exited','window_destroyed','thread_desktop_restored','child_desktop_closed','parent_desktop_closed','desktop_absent',
            'requester_exited','requester_thread_desktop_restored','requester_desktop_closed')
        report['passed']=bool(not report.get('reason') and report.get('result',{}).get('state')=='restored_minimized_for_development' and
            report.get('request_count')==1 and report.get('local_focus_unchanged') and report['working_desktop_unchanged'] and
            report.get('normal_result',{}).get('state')=='shown_normal_for_development' and
            report.get('normal_request_count')==1 and report.get('normal_visibility_verified') is True and
            report.get('requester_owner_match') is True and report.get('requester_distinct_process') is True and
            report.get('requester_focus_unchanged') is True and report['cleanup'].get('requester_exitcode')==0 and
            report['sources_unchanged'] and report['cleanup'].get('child_exitcode')==0 and not report['cleanup']['terminated'] and
            all(report['cleanup'].get(k) is True for k in required))
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-synthetic',action='store_true')
    args=parser.parse_args(argv)
    if not args.run_synthetic:print(json.dumps(dict(state='disabled')));return 0
    report=run();directory=ROOT/'.local/live-acceptance';path=directory/('window-restore-synthetic-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2)
    print(json.dumps(dict(path=str(path),passed=report['passed'],reason=report.get('reason'))))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
