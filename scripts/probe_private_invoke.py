"""Explicit one-shot Invoke probe on a bound account's session row; no body read."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.console_store import ConsoleStore, digest
from wechat_agent_bridge.protection import DPAPIProtector


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',action='store_true');p.add_argument('--candidate')
    p.add_argument('--method',choices=('invoke','legacy','select','window-message'),default='invoke')
    args=p.parse_args()
    if not args.run or not args.candidate:raise ConsoleError('explicit_navigation_probe_required')
    directory=ROOT/'.local/live-acceptance'
    auth=json.loads(DPAPIProtector().unprotect((directory/'dev-http-session.dpapi').read_bytes()))
    headers={'Origin':auth['origin'],'Content-Type':'application/json','Cookie':auth['cookie'],'X-CSRF-Token':auth['csrf']}
    def api(path,body=None):
        return json.load(urllib.request.urlopen(urllib.request.Request(auth['origin']+path,
            data=json.dumps(body).encode() if body is not None else None,headers=headers),timeout=12))
    status=api('/api/status')
    if not any(s['account_id']==auth['account'] and s['all_private'] for s in status['reply_scopes']):
        raise ConsoleError('all_private_scope_required')
    fp=status['background']['fingerprint']
    chats=api('/api/conversations',{'account':auth['account']})
    group=next(c for c in chats if c['id']==auth['conversation'] and c['kind']=='group')
    from wechat_agent_bridge.adapters.accessibility_gate import NativeGate
    from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
    from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
    from wechat_agent_bridge.adapters.background import snapshot, unchanged
    from wechat_agent_bridge.adapters.desktop_events import DesktopEventObserver
    from wechat_agent_bridge.adapters.private_navigation import navigate_once
    from comtypes.gen import UIAutomationClient as uia
    store=ConsoleStore(directory/'gate.sqlite3',DPAPIProtector())
    native=NativeGate(fp['hwnd']);backend=WindowsUIA();client=BackgroundUIAClient();raw=client._client
    started=time.monotonic()
    report={};request=None
    try:
        baseline=snapshot(fp['pid']);unchanged(baseline,baseline)
        if asdict(native.fingerprint())!=fp:raise ConsoleError('private_navigation_fingerprint_changed')
        root=client.element_from_handle(fp['hwnd'])
        matches=root.FindAll(uia.TreeScope_Descendants,raw.CreatePropertyCondition(
            uia.UIA_AutomationIdPropertyId,'session_item_'+args.candidate))
        if matches.Length!=1:raise ConsoleError('private_navigation_row_ambiguous')
        row=matches.GetElement(0);rid=tuple(row.GetRuntimeId())
        select=row.GetCurrentPattern(uia.UIA_SelectionItemPatternId).QueryInterface(uia.IUIAutomationSelectionItemPattern)
        geometry=None
        if args.method in ('select','window-message'):
            # A different semantic operation, with its own persistent attempt.
            # Require a contact card observed on this exact process/window.
            identity=store.get('private_runtime_probe',args.candidate+'-current-20261027')
            if (not identity or identity.get('fingerprint')!=fp or identity.get('label')!=args.candidate or
                    identity.get('identity',{}).get('state')!='contact_profile_verified' or
                    identity['identity']['target_controls']['session_runtime_id']!=list(rid)):
                raise ConsoleError('private_current_contact_identity_required')
            if args.method=='select':invoke_action=select.Select
            else:
                from wechat_agent_bridge.adapters.window_message_navigation import WindowMessages
                proof=json.loads((directory/'navigation-window-synthetic.json').read_text(encoding='utf-8'))
                for file in ('wechat_agent_bridge/adapters/window_message_navigation.py','scripts/test_navigation_window_messages.py'):
                    if proof.get('hashes',{}).get(file)!=hashlib.sha256((ROOT/file).read_bytes()).hexdigest():
                        raise ConsoleError('navigation_same_build_synthetic_required')
                if proof.get('state')!='passed':raise ConsoleError('navigation_synthetic_failed')
                window_messages=WindowMessages(fp['hwnd'],fp['pid'])
                def rectangle():
                    r=row.CurrentBoundingRectangle
                    return (r.left,r.top,r.right,r.bottom)
                geometry=rectangle();point=window_messages.point(geometry)
                invoke_action=lambda:window_messages.click(point)
        elif args.method=='invoke':
            invoke=row.GetCurrentPattern(uia.UIA_InvokePatternId).QueryInterface(uia.IUIAutomationInvokePattern)
            invoke_action=invoke.Invoke
        else:
            invoke=row.GetCurrentPattern(uia.UIA_LegacyIAccessiblePatternId).QueryInterface(uia.IUIAutomationLegacyIAccessiblePattern)
            if invoke.CurrentDefaultAction!='双击':raise ConsoleError('private_default_action_changed')
            invoke_action=invoke.DoDefaultAction
        source=backend.current_group_target(fp['pid'],fp['hwnd'],[group['name']])
        if source.get('draft_empty') is not True:raise ConsoleError('existing_draft_or_unknown')
        request=digest('private-row-'+args.method,json.dumps(fp,sort_keys=True),str(rid))
        if store.get('private_route_probe',request) is not None:raise ConsoleError('private_navigation_already_attempted')
        # Do not pause a working service when local/geometry preconditions fail.
        # The transaction rechecks both source and row after pausing.
        api('/api/pause',{})
        with DesktopEventObserver(fp['pid']) as observer:
            def check():
                if time.monotonic()-started>20:raise ConsoleError('private_navigation_deadline')
                if asdict(native.fingerprint())!=fp:raise ConsoleError('private_navigation_fingerprint_changed')
                if (directory/'STOP').exists():raise ConsoleError('private_navigation_emergency_stop')
                unchanged(baseline,snapshot(fp['pid']));observer.check();client.require_background_mode()
            class Port:
                def verify_source(self):
                    actual=backend.current_group_target(fp['pid'],fp['hwnd'],[group['name']])
                    if actual!=source or actual.get('draft_empty') is not True:raise ConsoleError('private_navigation_source_changed')
                def verify_row(self):
                    if (row.CurrentProcessId!=fp['pid'] or tuple(row.GetRuntimeId())!=rid or
                            row.CurrentAutomationId!='session_item_'+args.candidate or not row.CurrentIsEnabled):
                        raise ConsoleError('private_navigation_row_changed')
                    if geometry is not None and (rectangle()!=geometry or window_messages.point(geometry)!=point):
                        raise ConsoleError('private_navigation_geometry_changed')
                def invoke(self):invoke_action()
                def destination(self):
                    # A provider may acknowledge before its GUI thread handles
                    # the action. Observe a bounded interval; never re-invoke.
                    until=time.monotonic()+2
                    while True:
                        check()
                        headers=root.FindAll(uia.TreeScope_Descendants,raw.CreatePropertyCondition(
                            uia.UIA_ClassNamePropertyId,'mmui::ChatTitleBarChatSingleView'))
                        groups=root.FindAll(uia.TreeScope_Descendants,raw.CreatePropertyCondition(
                            uia.UIA_ClassNamePropertyId,'mmui::ChatTitleBarChatRoomView'))
                        result=dict(private_header=headers.Length==1 and groups.Length==0,
                            selected_row=bool(select.CurrentIsSelected),runtime_id=list(rid))
                        if (result['private_header'] and result['selected_row']) or time.monotonic()>=until:
                            return result
                        time.sleep(.05)
            def claim():
                store.put('private_route_probe',request,dict(state='unknown',attempted=True,fingerprint=fp,
                    method=args.method,candidate_digest=digest(args.candidate),created=time.time()))
                observer.set_phase('private_row_invoke')
            report=navigate_once(Port(),check,claim)
            report.update(events=list(observer.evidence.events),fingerprint=fp,request_id=request,method=args.method)
        # Observe the original group only. Do not restore it by another action.
        try:
            current=backend.current_group_target(fp['pid'],fp['hwnd'],[group['name']])
            report['source_unchanged']=current==source and current.get('draft_empty') is True
        except ConsoleError:report['source_unchanged']=False
        report['elapsed']=round(time.monotonic()-started,3)
        report['chat_bodies_read']=False;report['sent']=False
        store.put('private_route_probe',request,report)
        if report['source_unchanged'] and not report['events'] and not status['paused']:
            api('/api/resume',{});report['group_resumed']=True
        path=directory/('private-row-invoke-'+request+'.json')
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    finally:
        native.close();store.close()


if __name__=='__main__':main()
