"""One public Invoke of the fixed Contacts navigation button, never a contact.

Development-only: focus changes are recorded, not background qualifications.
The existing terminal navigation unknown and its lock are never cleared.
"""
import argparse
import ctypes as c
from dataclasses import asdict
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import inspect_contacts_navigation as metadata
from scripts import inspect_bound_search as readonly
from scripts import probe_background_draft as original
from scripts.development_window_restore import load_local_hashed
from scripts.development_navigation import cleanup_resources,input_tick
from scripts.private_test_scope import PrivateTestScope
from wechat_agent_bridge.console_models import ConsoleError

def navigation_transaction(port,checkpoint,claim,*,allowed=False):
    attempted=returned=False
    try:
        if not allowed:raise ConsoleError('contacts_navigation_explicit_opt_in_required')
        checkpoint();port.verify();before=port.page_structure()
        claim();checkpoint();port.verify()
        attempted=True;port.invoke();returned=True
        checkpoint();port.verify();after=port.page_structure()
        return dict(state='invoke_returned',action_executed=True,invoke_returned=True,
            before=before,after=after,structure_changed=before!=after,navigation_verified=False)
    except Exception as exc:
        return dict(state='unknown' if attempted else 'failed',action_executed=attempted,invoke_returned=returned,
            reason=str(exc) if isinstance(exc,ConsoleError) else 'contacts_navigation_operation_failed')


def sources():
    return dict(metadata.sources(),**{'scripts/development_contacts_navigation.py':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})


def require_inspection(value,fp):
    w=value.get('worker',{});items=w.get('contacts_items',[])
    if (value.get('kind')!='bound_contacts_navigation_metadata_readonly' or value.get('state')!='contacts_navigation_inspected' or
        value.get('fingerprint')!=fp or value.get('sources')!=metadata.sources() or value.get('sources_unchanged') is not True or
        value.get('cleanup_errors')!=[] or value.get('events')!=[] or value.get('patch_restored') is not True or
        value.get('worker_exited') is not True or value.get('action_executed') is not False or len(items)!=1 or
        items[0].get('class_name')!='mmui::XTabBarItem' or items[0].get('control_type')!=50000 or
        items[0].get('patterns',{}).get('Invoke') is not True or items[0].get('enabled') is not True or
        items[0].get('offscreen') is not False):raise ConsoleError('contacts_navigation_current_inspection_required')
    return items[0]['runtime_id'],w['navigation_runtime_id']


def worker(pipe,fp,label,recipient,expected):
    sys.coinit_flags=0
    try:
        with metadata.physical_coordinate_context():
            from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
            from scripts.probe_bound_semantics import unique_by_runtime
            from scripts.uia_client_bootstrap import require_safe_context
            from scripts.uia_context_evidence import inspect_context
            from scripts.uia_context_native import SelfMemory
            from comtypes.gen import UIAutomationClient as uia
            client=BackgroundUIAClient();root=client.element_from_handle(fp['hwnd'])
            node=unique_by_runtime(root,client._client,uia,50000,expected[0])
            pattern=node.GetCurrentPattern(uia.UIA_InvokePatternId).QueryInterface(uia.IUIAutomationInvokePattern)
            class Port:
                def verify(self):
                    client.require_background_mode()
                    now=metadata.collect(root,client._client,uia,fp,label,recipient)
                    if (now['navigation_runtime_id']!=expected[1] or len(now['contacts_items'])!=1 or
                        now['contacts_items'][0]['runtime_id']!=expected[0] or
                        now['contacts_items'][0]['class_name']!='mmui::XTabBarItem' or
                        not now['contacts_items'][0]['enabled'] or now['contacts_items'][0]['offscreen'] or
                        node.CurrentProcessId!=fp['pid'] or tuple(node.GetRuntimeId())!=tuple(expected[0])):
                        raise ConsoleError('contacts_navigation_button_changed')
                    memory=SelfMemory()
                    try:
                        context=inspect_context(memory,memory.image,c.cast(client._client,c.c_void_p).value,
                            c.cast(node,c.c_void_p).value,c.cast(pattern,c.c_void_p).value,False,pattern_kind='invoke')
                        memory.validate_source();require_safe_context(context)
                    finally:memory.close()
                def page_structure(self):
                    return metadata.page_structure(root,client._client,uia,fp['pid'])
                def invoke(self):pattern.Invoke()
            outcome=navigation_transaction(Port(),lambda:original._exchange(pipe,'checkpoint','contacts_navigation'),
                lambda:original._exchange(pipe,'claim','contacts_navigation'),allowed=True)
        pipe.send(dict(ok=True,result=outcome,dpi_context_restored=True))
    except Exception as exc:
        pipe.send(dict(ok=False,reason=str(exc) if isinstance(exc,ConsoleError) else 'contacts_navigation_worker_failed'))
    finally:pipe.close()


def run(args):
    if not (args.allow_development_navigation and args.allow_accessibility_patch and args.account and args.private_id and
        args.private_label and args.target_evidence and args.context_evidence and args.navigation_evidence):
        raise ConsoleError('contacts_navigation_explicit_scope_required')
    from scripts.probe_bound_uia_context import require_context_canary
    require_context_canary(args.context_evidence)
    from wechat_agent_bridge.adapters.accessibility_gate import NativeGate,AccessibilityGate
    from wechat_agent_bridge.adapters.background import snapshot
    from wechat_agent_bridge.adapters.desktop_events import DesktopEventObserver
    from wechat_agent_bridge.console_store import ConsoleStore
    from wechat_agent_bridge.protection import DPAPIProtector
    evidence,evidence_sha=load_local_hashed(args.target_evidence)
    inspection,inspection_sha=load_local_hashed(args.navigation_evidence)
    store=ConsoleStore(original.DIRECTORY/'gate.sqlite3',DPAPIProtector())
    native=gate=observer=process=parent=child=None;claimed=False;case=None;started=time.monotonic()
    report=dict(kind='development_fixed_contacts_navigation',state='failed',action_executed=False,invoke_returned=False,
        sources=sources(),cleanup_errors=[],chat_draft_read=False,message_text_read=False,contact_text_read=False,
        screenshot_captured=False,send_invoked=False,api_called=False,navigation_lock_released=False,background_qualified=False)
    try:
        binding=store.get('binding','account',{});fp_dict=binding.get('fingerprint',{})
        scope=PrivateTestScope(args.private_id,args.private_label)
        scope.resolve(binding,evidence,args.account,fp_dict,store,original.DIRECTORY);report.update(scope.metadata())
        nav=store.get('development_navigation','pending',{})
        attempt=store.get('development_navigation',nav.get('request','')) if isinstance(nav,dict) else None
        lock_path=original.DIRECTORY/'autofocus-draft.lock';token=lock_path.read_text(encoding='ascii')
        readonly.require_terminal_selection(nav,attempt,fp_dict,token)
        expected=require_inspection(inspection,fp_dict)
        case='contacts-invoke-'+hashlib.sha256(json.dumps([nav['request'],fp_dict,expected],sort_keys=True).encode()).hexdigest()
        if store.get('development_contacts_navigation',case) is not None:raise ConsoleError('contacts_navigation_already_attempted_no_retry')
        if any(store.get(k,'pending') for k in ('patch','thread_hook','draft_probe','development_window_restore','development_exact_search')):
            raise ConsoleError('contacts_navigation_other_recovery_required')
        report.update(request_id=case,fingerprint=fp_dict,navigation_evidence_sha256=inspection_sha)
        native=NativeGate(fp_dict['hwnd']);fp=native.fingerprint()
        if asdict(fp)!=fp_dict:raise ConsoleError('contacts_navigation_process_changed')
        baseline=snapshot(fp.pid);tick=input_tick()
        if baseline.locked:raise ConsoleError('desktop_locked_or_unavailable')
        observer=DesktopEventObserver(fp.pid).__enter__()
        def check():
            if (original.DIRECTORY/'STOP').exists():raise ConsoleError('contacts_navigation_emergency_stop')
            if time.monotonic()-started>20:raise ConsoleError('contacts_navigation_deadline')
            if sources()!=report['sources']:raise ConsoleError('contacts_navigation_sources_changed')
            if native.fingerprint()!=fp or store.get('binding','account')!=binding:raise ConsoleError('contacts_navigation_binding_changed')
            if store.get('development_navigation','pending')!=nav or store.get('development_navigation',nav['request'])!=attempt:
                raise ConsoleError('contacts_navigation_original_unknown_changed')
            if lock_path.read_text(encoding='ascii')!=token:raise ConsoleError('contacts_navigation_lock_changed')
            if load_local_hashed(args.target_evidence)[1]!=evidence_sha or load_local_hashed(args.navigation_evidence)[1]!=inspection_sha:
                raise ConsoleError('contacts_navigation_evidence_changed')
            scope.check(store);now=snapshot(fp.pid)
            if now.locked:raise ConsoleError('desktop_locked_or_unavailable')
            if input_tick()!=tick:raise ConsoleError('contacts_navigation_human_takeover')
            if now.clipboard_sequence!=baseline.clipboard_sequence:raise ConsoleError('contacts_navigation_clipboard_changed')
            if observer.evidence.failed:raise ConsoleError('contacts_navigation_observer_failed')
            if not claimed and (now.foreground,now.focus)!=(baseline.foreground,baseline.focus):raise ConsoleError('contacts_navigation_preclaim_focus_changed')
            return True
        gate=AccessibilityGate(native,allowed=True,save=lambda value:store.put('patch','pending',value))
        report['patch']=gate.enable(fp,check)
        ctx=mp.get_context('spawn');parent,child=ctx.Pipe()
        process=ctx.Process(target=worker,args=(child,fp_dict,args.private_label,args.private_id,expected));process.start();child.close()
        while True:
            check()
            if not parent.poll(.03):
                if not process.is_alive():raise ConsoleError('contacts_navigation_worker_disconnected')
                continue
            item=parent.recv()
            if item.get('kind')=='checkpoint':parent.send({'continue':check()})
            elif item.get('kind')=='claim' and not claimed:
                check();claimed=True;report.update(state='unknown',action_executed=None)
                store.put('development_contacts_navigation',case,report)
                observer.set_phase('development_fixed_contacts_invoke');parent.send({'continue':True})
            elif item.get('ok') is True:
                result=item['result']
                if result.get('action_executed') and not claimed:raise ConsoleError('contacts_navigation_unclaimed_result')
                report.update(result,dpi_context_restored=item.get('dpi_context_restored'));break
            else:raise ConsoleError(item.get('reason','contacts_navigation_invalid_protocol'))
        check()
    except Exception as exc:
        report.update(state='unknown' if claimed else 'failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'contacts_navigation_coordinator_failed')
    finally:
        if parent:
            try:parent.send({'continue':False})
            except (OSError,EOFError):pass
        cleanup_resources(report,process=process,gate=gate,observer=observer,resources=(('parent',parent),('child',child),('native',native)))
        report['sources_unchanged']=sources()==report['sources'];report['elapsed_seconds']=round(time.monotonic()-started,3)
        if not report['sources_unchanged']:report['state']='recovery_required';report['cleanup_errors'].append('contacts_navigation_sources_changed')
        try:
            if claimed:store.put('development_contacts_navigation',case,report)
        finally:store.close()
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--invoke-contacts',action='store_true');parser.add_argument('--allow-development-navigation',action='store_true')
    parser.add_argument('--allow-accessibility-patch',action='store_true')
    for name in ('account','private-id','private-label','target-evidence','context-evidence','navigation-evidence'):parser.add_argument('--'+name)
    args=parser.parse_args(argv)
    if not args.invoke_contacts:print(json.dumps(dict(state='disabled',action_executed=False)));return 0
    try:report=run(args)
    except Exception as exc:report=dict(state='failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'contacts_navigation_preparation_failed')
    path=original.DIRECTORY/('development-contacts-navigation-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print(json.dumps(dict(path=str(path),state=report['state'],reason=report.get('reason'))))
    return 0 if report['state']=='invoke_returned' else 1


if __name__=='__main__':raise SystemExit(main())
