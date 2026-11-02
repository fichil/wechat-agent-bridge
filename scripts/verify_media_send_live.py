"""Explicit-target media acceptance. No UI input; native evidence AND archive are required.

Run in the same interactive Windows session as WeChat, after the operator names
the account and conversation. The archive is opened read-only. No AI settings change.
"""
import argparse
from contextlib import closing
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime, timezone
import io
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


class DesktopObservation:
    def __init__(self,pid):
        self.pid=pid;self.user=c.WinDLL('user32',use_last_error=True)
        self.user.GetForegroundWindow.restype=w.HWND
        self.user.GetWindowThreadProcessId.argtypes=[w.HWND,c.POINTER(w.DWORD)]
        for name in ('IsWindowVisible','IsIconic'): getattr(self.user,name).argtypes=[w.HWND]
        self.user.GetWindowRect.argtypes=[w.HWND,c.POINTER(w.RECT)]
        self.callback=c.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
        self.user.EnumWindows.argtypes=[self.callback,w.LPARAM]

    def snapshot(self):
        windows=[]
        def collect(hwnd,_):
            pid=w.DWORD();self.user.GetWindowThreadProcessId(hwnd,c.byref(pid))
            if pid.value==self.pid:
                rect=w.RECT();self.user.GetWindowRect(hwnd,c.byref(rect))
                windows.append(dict(hwnd=hwnd,visible=bool(self.user.IsWindowVisible(hwnd)),minimized=bool(self.user.IsIconic(hwnd)),rect=[rect.left,rect.top,rect.right,rect.bottom]))
            return True
        self.user.EnumWindows(self.callback(collect),0)
        return dict(windows=sorted(windows,key=lambda x:x['hwnd']),foreground=self.user.GetForegroundWindow(),clipboard=self.user.GetClipboardSequenceNumber())

    def drafts(self):
        command=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/observe_send_drafts.ps1'),'-ProcessIds',str(self.pid)]
        try:
            result=subprocess.run(command,capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
            return json.loads(result.stdout.decode('utf-8-sig')) if result.returncode==0 else []
        except (OSError,ValueError,subprocess.TimeoutExpired): return []

    def database_drafts(self, instance, wxid):
        from wechat_account_probe.models import Instance
        from wechat_conversations.reader import SessionReader
        from wechat_messages.cache import DatabaseCache
        session=SessionReader(Instance(**instance),wxid)
        try:
            session.bind(time.monotonic()+60)
            session.cache=DatabaseCache(session)
            with session.cache.database('session/session.db') as db:
                values=[(r[0],r[1].hex() if isinstance(r[1],bytes) else r[1]) for r in db.execute('SELECT username,draft FROM SessionTable ORDER BY username')]
            return dict(available=True,count=len(values),nonempty=sum(bool(v[1]) for v in values),sha256=hashlib.sha256(json.dumps(values,ensure_ascii=True).encode()).hexdigest())
        finally: session.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--account-id',required=True)
    parser.add_argument('--conversation-id',required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--resume',action='store_true',help='Resume only a definitely unsent item; never retry unknown/sending.')
    args=parser.parse_args()
    if not args.run: parser.print_help();return 0
    from PIL import Image,ImageDraw
    from wechat_account_probe import discover_accounts
    from wechat_account_probe.observation import Observation
    from wechat_sender.worker import modules
    from wechat_sender.media_files import stage,release
    from backend.app.reply_media import validate_media
    from backend.app.delivery import NativeTransport
    def query(sql,values=()):
        with closing(sqlite3.connect(args.archive.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            return [dict(r) for r in db.execute(sql,values)]
    accounts=query('SELECT wxid FROM accounts WHERE account_id=?',(args.account_id,))
    targets=query('SELECT source_id,payload FROM conversations WHERE account_id=? AND conversation_id=?',(args.account_id,args.conversation_id))
    if len(accounts)!=1 or len(targets)!=1: raise RuntimeError('Exact account and conversation required')
    if query("SELECT 1 FROM deliveries WHERE account_id=? AND status IN ('queued','sending') LIMIT 1",(args.account_id,)):
        raise RuntimeError('Existing account outbox is busy; no test was sent')
    wxid=accounts[0]['wxid'];target=targets[0]['source_id']
    found=[a for a in discover_accounts()['accounts'] if a.get('wxid')==wxid and len(a.get('instances',[]))==1]
    if len(found)!=1: raise RuntimeError('Account is not uniquely online')
    instance=found[0]['instances'][0]
    kernel=c.WinDLL('kernel32');ours=w.DWORD();theirs=w.DWORD()
    kernel.ProcessIdToSessionId(kernel.GetCurrentProcessId(),c.byref(ours));kernel.ProcessIdToSessionId(instance['pid'],c.byref(theirs))
    if ours.value!=theirs.value: raise RuntimeError('Acceptance observer must share the WeChat desktop session')
    binding=dict(account=wxid,pid=instance['pid'],created=instance['created'])
    transport=NativeTransport();identity=transport.inspect(binding)
    if identity.get('core_sha256')!='a81c4770350a4edf3e774413da832a785d7aac50025471b30ea26d1f846b9fb4' or not all(identity.get(k) for k in ('ready','send_image','send_sticker')):
        raise RuntimeError('Production media fingerprint/capability check failed: '+str(identity.get('code')))
    if args.resume:
        report=json.loads(args.report.read_text(encoding='utf-8'))
        if report['account_id']!=args.account_id or report['conversation_id']!=args.conversation_id or report['target']!=target:
            raise RuntimeError('Acceptance target changed')
        if any(p['status'] in ('unknown','sending') for p in report['parts']): raise RuntimeError('Uncertain result cannot be retried')
    else:
        report=dict(account_id=args.account_id,conversation_id=args.conversation_id,target=target,marker='BRIDGE_MEDIA_TEST_'+uuid4().hex[:12],identity=identity,parts=[],passed=False)
        args.report.parent.mkdir(parents=True,exist_ok=True)
        with args.report.open('x',encoding='utf-8') as stream: json.dump(report,stream)
    def save():
        temporary=args.report.with_suffix('.tmp');temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(args.report)
    png=Image.new('RGB',(320,180),'#18785d');ImageDraw.Draw(png).text((16,75),report['marker'],fill='white')
    stream=io.BytesIO();png.save(stream,format='PNG');image=validate_media(stream.getvalue())
    manifest=json.loads((ROOT/'backend/assets/dsh-pet/manifest.json').read_text(encoding='utf-8'))
    asset=next(i for i in manifest['items'] if i['name']=='可爱')
    sticker=validate_media((ROOT/'backend/assets/dsh-pet'/asset['file']).read_bytes(),'sticker')
    frames=[Image.new('RGB',(160,100),color) for color in ('#18785d','#ffd65c','#3479aa')]
    stream=io.BytesIO();frames[0].save(stream,format='GIF',save_all=True,append_images=frames[1:],duration=300,loop=0)
    animation=validate_media(stream.getvalue(),'sticker')
    parts=[('text',report['marker']+' 开始图文顺序验收',None),('image','',image),('sticker','',sticker),('sticker','',animation),('text',report['marker']+' 图文顺序验收结束',None)]
    observer=DesktopObservation(instance['pid']);report['drafts_before']=observer.drafts()
    report['database_drafts_before']=observer.database_drafts(instance,wxid);save()
    for index,(kind,text,media) in enumerate(parts):
        if index<len(report['parts']) and report['parts'][index]['status']=='sent': continue
        item=dict(index=index,kind=kind,status='sending',started_at=datetime.now(timezone.utc).isoformat())
        if index<len(report['parts']): report['parts'][index]=item
        else: report['parts'].append(item)
        save() # A crash after this write is uncertain, never automatically retried.
        job=dict(target=target,text=text,kind=kind)
        if media: job['staged_media']=stage(uuid4().hex,media)
        before=observer.snapshot()
        with Observation([instance['pid']]) as events: result=transport.send(binding,job)
        after=observer.snapshot()
        evidence=result.get('evidence',{});matched=None
        deadline=time.monotonic()+50
        while time.monotonic()<deadline and result.get('status') in ('sent','accepted'):
            rows=query("SELECT message_id,payload,sort_key,server_id,source_ref FROM messages WHERE account_id=? AND conversation_id=? AND json_extract(payload,'$.is_self')=1 AND json_extract(payload,'$.type')=? AND unixepoch(json_extract(payload,'$.sent_at'))>=unixepoch(?)",(args.account_id,args.conversation_id,kind,item['started_at']))
            if kind=='text': matches=[r for r in rows if json.loads(r['payload']).get('content',{}).get('text')==text]
            else: matches=[r for r in rows if json.loads(r['source_ref']).get('local_id')==evidence.get('local_id')
                and int(datetime.fromisoformat(json.loads(r['payload'])['sent_at']).timestamp())==evidence.get('create_time')
                and r['server_id'].isdigit() and int(r['server_id'])>0 and evidence.get('target')==target
                and evidence.get('message_type')==(47 if kind=='sticker' else 3)]
            if len(matches)==1: matched=matches[0];break
            time.sleep(.3)
        item.update(status='sent' if matched else 'failed' if result.get('status')=='failed' else 'unknown',result=result,
            archive={k:matched[k] for k in ('message_id','server_id','sort_key','source_ref')} if matched else None,
            stable=before==after and bool(before['windows']),before=before,after=after,observation=events.result,
            module_unloaded='bridge_text.dll' not in modules(instance['pid']))
        if media and result.get('resources_released') and item['status'] in ('sent','failed'): release(job['staged_media'])
        save();print(json.dumps(dict(index=index,kind=kind,status=item['status'],native_status=result.get('status'),code=result.get('code'),archive_match=bool(matched))),flush=True)
        if item['status']!='sent': break
    report['drafts_after']=observer.drafts()
    report['database_drafts_after']=observer.database_drafts(instance,wxid)
    nonempty=any(e.get('length',0)>0 for row in report['drafts_before'] for e in row.get('edits',[]))
    report['drafts_unchanged']=(report['database_drafts_before']==report['database_drafts_after'] and
        (report['drafts_before']==report['drafts_after'] if nonempty else True))
    successful=len(report['parts'])==5 and all(p['status']=='sent' for p in report['parts'])
    ordered=successful and [p['archive']['sort_key'] for p in report['parts']]==sorted(p['archive']['sort_key'] for p in report['parts'])
    if successful:
        from wechat_account_probe.models import Instance
        from wechat_conversations.reader import SessionReader
        from wechat_messages.reader import MessageReader
        import base64
        session=SessionReader(Instance(**instance),wxid);reader=MessageReader(session)
        report['media_readback']=[]
        try:
            for item in report['parts']:
                expected=parts[item['index']][2]
                if not expected: continue
                try:
                    value=reader.image(dict(source_id=target,reference=json.loads(item['archive']['source_ref'])),time.monotonic()+90)
                    if value.get('status')!='available' and item['kind']=='sticker':
                        # Outgoing custom GIF XML may have only an MD5, with no CDN URL.
                        # Correlate the archived digest with the verified immutable send snapshot.
                        from wechat_messages.stickers import sticker_reference
                        ref=json.loads(item['archive']['source_ref'])
                        with session.cache.database(ref['shard']) as db:
                            row=db.execute('SELECT message_content,compress_content FROM '+reader.table(target)+' WHERE local_id=? AND server_id=?',(ref['local_id'],int(ref['server_id']))).fetchone()
                        md5=sticker_reference(dict(row)).get('md5') if row else None
                        report['media_readback'].append(dict(index=item['index'],method='archive_md5',archive_md5=md5,sha256=expected['sha256'],frame_count=expected['frame_count'],matches=md5==hashlib.md5(expected['data']).hexdigest()))
                        save();continue
                    actual=validate_media(base64.b64decode(value['data_base64']),item['kind'])
                    report['media_readback'].append(dict(index=item['index'],method='original',sha256=actual['sha256'],frame_count=actual['frame_count'],matches=actual['sha256']==expected['sha256']))
                except Exception as exc: report['media_readback'].append(dict(index=item['index'],matches=False,error=type(exc).__name__))
                save()
        finally: reader.close();session.close()
    report.update(transport_passed=bool(ordered),passed=bool(ordered and all(v['matches'] for v in report.get('media_readback',[])) and len(report.get('media_readback',[]))==3 and report['drafts_unchanged'] is True and all(p['stable'] and p['module_unloaded'] and p['observation']['available'] and not p['observation']['wechat_foreground_events'] and not p['observation']['wechat_focus_events'] for p in report['parts'])))
    save();print(json.dumps(dict(report=str(args.report),transport_passed=report['transport_passed'],passed=report['passed'],drafts_unchanged=report['drafts_unchanged'])),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
