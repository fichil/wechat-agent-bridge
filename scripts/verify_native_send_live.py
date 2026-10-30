"""Explicit filehelper-only send acceptance; never activates or edits any window."""
import argparse
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from urllib.request import Request,build_opener,ProxyHandler
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bridge_paths import DATA


def run_acceptance(api):
    from wechat_sender.compatibility import build_identity,CORE_HASHES,digest
    from wechat_sender.worker import modules,code_windows
    from wechat_account_probe.observation import Observation
    from backend.app.delivery import NativeTransport
    accounts=[a for a in api.post('accounts/query')['accounts'] if a.get('wxid') and len(a.get('instances',[]))==1]
    if not accounts: raise RuntimeError('No uniquely bound online accounts')
    return _run(accounts, api)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    args=parser.parse_args()
    if not args.run: parser.print_help();return 0
    import os
    from getpass import getpass
    from bridge_portable.local_api import LocalApi
    origin=os.environ.get('BRIDGE_PUBLIC_ORIGIN','http://127.0.0.1:5173')
    with LocalApi(origin,input('Administrator username [admin]: ').strip() or 'admin',getpass('Password: ')) as api:
        summary, output = run_acceptance(api)
    print(json.dumps(dict(report=str(output), passed=summary['passed']),ensure_ascii=False))
    return 0 if summary['passed'] else 1


def _run(accounts, api):
    from wechat_sender.compatibility import build_identity,CORE_HASHES,digest
    from wechat_sender.worker import modules,code_windows
    from wechat_account_probe.observation import Observation
    from backend.app.delivery import NativeTransport
    user=c.WinDLL('user32',use_last_error=True)
    user.GetForegroundWindow.restype=w.HWND
    user.GetWindowThreadProcessId.argtypes=[w.HWND,c.POINTER(w.DWORD)]
    user.IsWindowVisible.argtypes=[w.HWND];user.IsIconic.argtypes=[w.HWND]
    user.GetWindowRect.argtypes=[w.HWND,c.POINTER(w.RECT)]
    user.GetCursorPos.argtypes=[c.POINTER(w.POINT)]
    class LastInput(c.Structure): _fields_=[('size',w.UINT),('tick',w.DWORD)]
    user.GetLastInputInfo.argtypes=[c.POINTER(LastInput)]
    callback_type=c.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
    user.EnumWindows.argtypes=[callback_type,w.LPARAM]
    pids=[a['instances'][0]['pid'] for a in accounts]
    def snapshot():
        windows=[]
        def collect(hwnd,_):
            pid=w.DWORD();user.GetWindowThreadProcessId(hwnd,c.byref(pid))
            if pid.value in pids:
                rect=w.RECT();user.GetWindowRect(hwnd,c.byref(rect))
                windows.append(dict(hwnd=hwnd,pid=pid.value,visible=bool(user.IsWindowVisible(hwnd)),minimized=bool(user.IsIconic(hwnd)),rect=[rect.left,rect.top,rect.right,rect.bottom]))
            return True
        cb=callback_type(collect);user.EnumWindows(cb,0)
        point=w.POINT();user.GetCursorPos(c.byref(point))
        last=LastInput(c.sizeof(LastInput),0);user.GetLastInputInfo(c.byref(last))
        return dict(windows=sorted(windows,key=lambda x:x['hwnd']),foreground=user.GetForegroundWindow(),cursor=[point.x,point.y],clipboard=user.GetClipboardSequenceNumber(),last_input=last.tick)
    def drafts():
        # Read ValuePattern only, never SetValue/SetFocus/Invoke. A timeout kills this observer alone.
        command='& '+"'"+str(ROOT/'scripts/observe_send_drafts.ps1').replace("'", "''")+"' -ProcessIds "+','.join(map(str,pids))
        try:
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
            return json.loads(result.stdout.decode('utf-8-sig')) if result.returncode==0 else []
        except (OSError,ValueError,subprocess.TimeoutExpired): return []
    transport=NativeTransport(); reports=[]
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output=DATA/'native-send-acceptance'/('live-'+stamp+'-'+uuid4().hex[:6]+'.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    summary=dict(passed=False,dll_sha256=build_identity(),accounts=[],samples=reports)
    before_drafts=drafts()
    for index,a in enumerate(accounts):
        i=a['instances'][0]; binding=dict(pid=i['pid'],created=i['created'],account=a['wxid'])
        identity=transport.inspect(binding)
        if identity.get('core_sha256') not in CORE_HASHES: raise RuntimeError('Native compatibility failed: '+str(identity.get('code')))
        core=Path(modules(i['pid'])['weixin.dll'][1]); backup=core.with_name(core.name+'.h.bak')
        if identity['core_sha256'] != 'e0428a7b691853de496532523c2c09aa095f90941c37c9f4cfb3f137f3c66556':
            if not backup.exists() or digest(backup)!='e0428a7b691853de496532523c2c09aa095f90941c37c9f4cfb3f137f3c66556':
                raise RuntimeError('Verified original backup required for patched-build acceptance')
            if code_windows(core)!=code_windows(backup): raise RuntimeError('Borrowed code entries differ from original')
        marker=f'BRIDGE_TEST_{stamp}_{index}_{uuid4().hex[:8]} 无感发送验证 🙂'
        before=snapshot()
        with Observation(pids) as observation:
            result=transport.invoke(dict(binding,operation='send',acceptance_test=True,target='filehelper',text=marker))
        after=snapshot()
        matched=False; aid=a['account_id']
        # The running service remains the sole writer. This connection is strictly read-only.
        deadline=time.monotonic()+50
        while time.monotonic()<deadline:
            db=sqlite3.connect((DATA/'bridge.sqlite3').as_uri()+'?mode=ro',uri=True)
            try:
                count=db.execute("SELECT count(*) FROM messages m JOIN conversations c USING(account_id,conversation_id) WHERE m.account_id=? AND c.source_id='filehelper' AND json_extract(m.payload,'$.content.text')=? AND json_extract(m.payload,'$.is_self')=1",(aid,marker)).fetchone()[0]
                matched=count==1
            finally: db.close()
            if matched or result.get('status')!='sent': break
            time.sleep(1)
        stable=(before['windows']==after['windows'] and before['foreground']==after['foreground']
                and before['clipboard']==after['clipboard'] and before['cursor']==after['cursor'] and before['last_input']==after['last_input'])
        noninterference=(observation.result.get('available') and not observation.result.get('wechat_foreground_events')
                         and not observation.result.get('wechat_focus_events') and stable)
        removed='bridge_text.dll' not in modules(i['pid'])
        sample=dict(account_id=aid,account_hash=hashlib.sha256(a['wxid'].encode()).hexdigest(),result=result,
                    archive_match=matched,module_unloaded=removed,before=before,after=after,observation=observation.result,
                    noninterference='passed' if noninterference else 'inconclusive')
        reports.append(sample)
        output.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(account_index=index,status=result.get('status'),code=result.get('code'),archive_match=matched,module_unloaded=removed,noninterference=sample['noninterference'])),flush=True)
        if result.get('status')!='sent': break
    after_drafts=drafts()
    observed={d['pid'] for d in before_drafts if any(e.get('length',0)>0 for e in d.get('edits',[]))}
    summary.update(drafts_before=before_drafts,drafts_after=after_drafts,
                   drafts_unchanged=(before_drafts==after_drafts) if observed==set(pids) else None,
                   draft_observation='complete' if observed==set(pids) else 'inconclusive')
    summary['passed']=len(reports)==len(accounts) and all(r['result'].get('status')=='sent' and r['archive_match'] and r['module_unloaded'] and r['noninterference']=='passed' for r in reports)
    summary['core_sha256']=reports[0]['result'].get('core_sha256') if reports else None
    summary['accounts']=[a['wxid'] for a in accounts]
    # This report alone never enables the sender. All required scenarios must be reviewed first.
    output.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(report=str(output),transport_passed=summary['passed'],drafts_unchanged=summary['drafts_unchanged'],draft_windows=len(before_drafts))),flush=True)
    return summary, output


if __name__=='__main__': raise SystemExit(main())
