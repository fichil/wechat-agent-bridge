"""Explicit own hidden-window test; accepts no external window/process."""
import argparse
import ctypes as c
from ctypes import wintypes as w
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def receiver(pipe):
    u, k = c.WinDLL('user32'), c.WinDLL('kernel32')
    Proc = c.WINFUNCTYPE(w.LPARAM, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
    class Class(c.Structure):
        _fields_ = [('style',w.UINT),('proc',Proc),('clsExtra',c.c_int),('wndExtra',c.c_int),
                    ('instance',w.HINSTANCE),('icon',w.HICON),('cursor',w.HANDLE),
                    ('background',w.HBRUSH),('menu',w.LPCWSTR),('name',w.LPCWSTR)]
    u.DefWindowProcW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];u.DefWindowProcW.restype=w.LPARAM
    u.CreateWindowExW.argtypes=[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,c.c_int,c.c_int,c.c_int,c.c_int,w.HWND,w.HMENU,w.HINSTANCE,w.LPVOID]
    u.CreateWindowExW.restype=w.HWND;u.DestroyWindow.argtypes=[w.HWND]
    u.RegisterClassW.argtypes=[c.POINTER(Class)];u.UnregisterClassW.argtypes=[w.LPCWSTR,w.HINSTANCE]
    u.GetMessageW.argtypes=[c.POINTER(w.MSG),w.HWND,w.UINT,w.UINT]
    u.DispatchMessageW.argtypes=[c.POINTER(w.MSG)];u.DispatchMessageW.restype=w.LPARAM
    k.GetModuleHandleW.restype=w.HMODULE
    count=[];slow=[False]
    def dispatch(hwnd,msg,wp,lp):
        if msg in (0x201,0x202):
            if slow[0]:time.sleep(.2)
            count.append((msg,wp,lp));return 0
        if msg==0x8001:
            pipe.send(count[:]);return 0
        if msg==0x8002:slow[0]=True;return 0
        if msg==0x10:u.DestroyWindow(hwnd);return 0
        if msg==2:u.PostQuitMessage(0);return 0
        return u.DefWindowProcW(hwnd,msg,wp,lp)
    cb=Proc(dispatch);name='M1NavigationOwn'+str(os.getpid());instance=k.GetModuleHandleW(None)
    klass=Class(0,cb,0,0,instance,None,None,None,None,name)
    if not u.RegisterClassW(c.byref(klass)):raise RuntimeError('register')
    hwnd=u.CreateWindowExW(0,name,'synthetic',0,0,0,320,200,None,None,instance,None)
    if not hwnd:raise RuntimeError('create')
    pipe.send((hwnd,os.getpid()));msg=w.MSG()
    while u.GetMessageW(c.byref(msg),None,0,0)>0:u.DispatchMessageW(c.byref(msg))
    u.UnregisterClassW(name,instance);pipe.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--run-synthetic',action='store_true');args=p.parse_args()
    if not args.run_synthetic:print('{"state":"disabled"}');return
    from wechat_agent_bridge.adapters.window_message_navigation import WindowMessages
    from wechat_agent_bridge.adapters.background import snapshot
    from wechat_agent_bridge.console_models import ConsoleError
    ctx=mp.get_context('spawn');parent,child=ctx.Pipe();proc=ctx.Process(target=receiver,args=(child,));proc.start();child.close()
    hwnd=None;result={}
    try:
        if not parent.poll(8):raise RuntimeError('startup_timeout')
        hwnd,pid=parent.recv();port=WindowMessages(hwnd,pid);before=snapshot(pid)
        port.click((20,30))
        u=port.u;u.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
        u.PostMessageW(hwnd,0x8001,0,0)
        if not parent.poll(3):raise RuntimeError('ack_timeout')
        ack=parent.recv();result['exact_pair']=ack==[(0x201,1,20|(30<<16)),(0x202,0,20|(30<<16))]
        result['desktop_unchanged']=before==snapshot(pid)
        u.PostMessageW(hwnd,0x8002,0,0);time.sleep(.05)
        try:port.send(0x201,1,0,timeout_ms=30)
        except ConsoleError as e:result['timeout_detected']=str(e)=='navigation_message_timeout_or_failure'
        else:result['timeout_detected']=False
    finally:
        if hwnd:
            u=c.WinDLL('user32');u.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];u.PostMessageW(hwnd,0x10,0,0)
        proc.join(3)
        if proc.is_alive():proc.terminate();proc.join(3)
        result['worker_exited']=not proc.is_alive();result['exit_code']=proc.exitcode
        parent.close()
    result['state']='passed' if all(result.get(k) is True for k in ('exact_pair','desktop_unchanged','timeout_detected','worker_exited')) and result['exit_code']==0 else 'failed'
    print(json.dumps(result))


if __name__=='__main__':main()
