"""Opt-in development update of one proven search field to the exact recipient ID.

Separate from the read-only inspector. No Enter, contact selection, chat/draft
access, send or upload exists. Unknown navigation and its original lock remain.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import platform
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import inspect_bound_search as readonly
from scripts import probe_background_draft as original
from scripts.development_window_restore import load_local_hashed
from scripts.development_navigation import cleanup_resources,input_tick
from scripts.contact_profile_identity import unchanged_readonly,sha
from scripts.private_test_scope import PrivateTestScope
from wechat_agent_bridge.console_models import ConsoleError


def exact_search_transaction(port,checkpoint,claim,prior,target,*,allowed=False):
    attempted=returned=False
    try:
        if not allowed:raise ConsoleError('exact_search_explicit_opt_in_required')
        checkpoint();port.verify();port.guard_context()
        value=port.value()
        if value==target:return dict(state='already_exact_search',action_executed=False,setter_returned=False)
        if value!=prior:raise ConsoleError('exact_search_preserve_changed_query')
        claim();checkpoint();port.verify();port.guard_context()
        if port.value()!=prior:raise ConsoleError('exact_search_changed_before_call')
        attempted=True
        port.set_value(target);returned=True
        checkpoint();port.verify();port.guard_context()
        if port.value()!=target:raise ConsoleError('exact_search_readback_missing')
        checkpoint()
        return dict(state='exact_search_updated',action_executed=True,setter_returned=True)
    except Exception as exc:
        return dict(state='unknown' if attempted else 'failed',action_executed=attempted,setter_returned=returned,
            reason=str(exc) if isinstance(exc,ConsoleError) else 'exact_search_operation_failed')


def sources():
    paths=('scripts/development_exact_search.py','scripts/probe_exact_search.py','scripts/inspect_bound_search.py',
        'scripts/probe_uia_autofocus.py','scripts/test_focus_etw.py','scripts/private_test_scope.py',
        'scripts/development_navigation.py','scripts/uia_client_bootstrap.py','scripts/uia_context_evidence.py',
        'scripts/uia_context_native.py','wechat_agent_bridge/adapters/uia_client.py')
    return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}


def require_synthetic(path):
    r,_digest=load_local_hashed(path)
    if (r.get('kind')!='owned_exact_search_probe' or r.get('passed') is not True or r.get('sources')!=sources() or
        r.get('contains_real_client') is not False or r.get('os')!=platform.version() or
        r.get('sources_unchanged') is not True or r.get('working_desktop_unchanged') is not True or
        r.get('uia_dll_unchanged') is not True or r.get('mta_verified') is not True or
        r.get('operation',{}).get('state')!='exact_search_updated' or
        r.get('operation',{}).get('setter_returned') is not True or
        r.get('operation',{}).get('set_calls')!=1 or r.get('operation',{}).get('native_full_readback_equal') is not True or
        r.get('operation',{}).get('context_verified') is not True or r.get('operation',{}).get('focus_unchanged') is not True or
        r.get('cleanup',{}).get('terminated') is not False or r.get('cleanup',{}).get('errors')!=[] or
        any(r.get('cleanup',{}).get(k) is not True for k in (
            'child_exited','native_cleanup_confirmed','owned_windows_absent','desktop_closed','desktop_absent'))):
        raise ConsoleError('exact_search_owned_evidence_required')


def validate_inspection(report,fp,label,recipient,*,current_sources=True):
    w=report.get('worker',{})
    if (report.get('kind')!='bound_search_control_readonly' or report.get('state')!='search_control_inspected' or
        report.get('fingerprint')!=fp or report.get('recipient_id_sha256')!=sha(recipient) or
        report.get('target_label_sha256')!=sha(label) or report.get('sources_unchanged') is not True or
        (current_sources and report.get('sources')!=readonly.sources()) or
        report.get('patch_restored') is not True or report.get('worker_exited') is not True or
        report.get('cleanup_errors')!=[] or report.get('events')!=[] or
        report.get('selection_unknown_preserved') is not True or report.get('action_executed') is not False or
        report.get('screenshot_captured') is not False or report.get('message_text_read') is not False or
        report.get('chat_draft_read') is not False or w.get('search_class')!=readonly.SEARCH_CLASS or
        w.get('readonly') is not False or w.get('query_kind') not in ('display_label','recipient_id')):
        raise ConsoleError('exact_search_verified_inspection_required')
    rid=w.get('search_runtime_id')
    if not isinstance(rid,list) or not 1<=len(rid)<=16 or any(type(x) is not int for x in rid):
        raise ConsoleError('exact_search_runtime_id_invalid')
    return rid


def worker(pipe,fp,label,recipient,runtime,reconcile):
    sys.coinit_flags=0
    try:
        from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
        from scripts.probe_bound_semantics import unique_by_runtime
        from scripts.uia_client_bootstrap import read_pattern_context,require_safe_context
        client=BackgroundUIAClient();root=client.element_from_handle(fp['hwnd'])
        from comtypes.gen import UIAutomationClient as uia
        initial=readonly.inspect_control(root,client._client,uia,fp,label,recipient)
        if initial['search_runtime_id']!=runtime:raise ConsoleError('exact_search_field_changed')
        node=unique_by_runtime(root,client._client,uia,uia.UIA_EditControlTypeId,runtime)
        pattern=node.GetCurrentPattern(uia.UIA_ValuePatternId).QueryInterface(uia.IUIAutomationValuePattern)
        class Port:
            def verify(self):
                client.require_background_mode()
                if readonly.inspect_control(root,client._client,uia,fp,label,recipient)['search_runtime_id']!=runtime:
                    raise ConsoleError('exact_search_field_changed')
                if (tuple(node.GetRuntimeId())!=tuple(runtime) or node.CurrentProcessId!=fp['pid'] or
                    node.CurrentClassName!=readonly.SEARCH_CLASS or node.CurrentAutomationId!=''):
                    raise ConsoleError('exact_search_bound_node_changed')
                if type(pattern.CurrentIsReadOnly) not in (bool,int) or pattern.CurrentIsReadOnly!=0:
                    raise ConsoleError('exact_search_field_readonly')
            def guard_context(self):require_safe_context(read_pattern_context(client._client,node,pattern))
            def value(self):
                value=pattern.CurrentValue
                if not isinstance(value,str) or len(value)>128:raise ConsoleError('exact_search_value_unexpected')
                return value
            def set_value(self,value):pattern.SetValue(value)
        port=Port()
        if reconcile:
            port.verify()
            if port.value()!=recipient:raise ConsoleError('exact_search_still_unconfirmed')
            outcome=dict(state='exact_search_observed_after_unknown',action_executed=False,setter_returned=False)
        else:
            outcome=exact_search_transaction(port,lambda:original._exchange(pipe,'checkpoint','exact_search'),
                lambda:original._exchange(pipe,'claim','set_exact_recipient_query'),label,recipient,allowed=True)
        result=readonly.inspect_control(root,client._client,uia,fp,label,recipient)
        pipe.send(dict(ok=True,result=result,operation=outcome))
    except Exception as exc:
        pipe.send(dict(ok=False,reason=str(exc) if isinstance(exc,ConsoleError) else 'exact_search_worker_failed'))
    finally:pipe.close()


def run(args):
    reconcile=args.inspect_after_attempt
    if not (args.allow_accessibility_patch and args.account and args.private_id and args.private_label and
        args.target_evidence and args.context_evidence and args.search_evidence and
        (reconcile or args.allow_development_search_write)):
        raise ConsoleError('exact_search_explicit_scope_required')
    if not reconcile:
        if not args.synthetic_search_evidence:raise ConsoleError('exact_search_owned_evidence_required')
        require_synthetic(args.synthetic_search_evidence)
    from scripts.probe_bound_uia_context import require_context_canary
    require_context_canary(args.context_evidence)
    from wechat_agent_bridge.adapters.accessibility_gate import NativeGate,AccessibilityGate
    from wechat_agent_bridge.adapters.background import snapshot
    from wechat_agent_bridge.adapters.desktop_events import DesktopEventObserver
    from wechat_agent_bridge.console_store import ConsoleStore
    from wechat_agent_bridge.protection import DPAPIProtector
    evidence,evidence_sha=load_local_hashed(args.target_evidence)
    inspection,inspection_sha=load_local_hashed(args.search_evidence)
    store=ConsoleStore(original.DIRECTORY/'gate.sqlite3',DPAPIProtector())
    native=gate=observer=process=parent=child=None;claimed=False;received=False;case=None;pending_search=None
    started=time.monotonic()
    report=dict(kind='development_exact_recipient_search',state='failed',action_executed=False,setter_returned=False,
        sources=sources(),cleanup_errors=[],chat_draft_read=False,message_text_read=False,screenshot_captured=False,
        send_invoked=False,api_called=False,navigation_lock_released=False,background_qualified=False)
    try:
        binding=store.get('binding','account',{});fp_dict=binding.get('fingerprint',{})
        scope=PrivateTestScope(args.private_id,args.private_label)
        scope.resolve(binding,evidence,args.account,fp_dict,store,original.DIRECTORY);report.update(scope.metadata())
        nav=store.get('development_navigation','pending',{})
        attempt=store.get('development_navigation',nav.get('request','')) if isinstance(nav,dict) else None
        lock_path=original.DIRECTORY/'autofocus-draft.lock';token=lock_path.read_text(encoding='ascii')
        readonly.require_terminal_selection(nav,attempt,fp_dict,token)
        runtime=validate_inspection(inspection,fp_dict,args.private_label,args.private_id,current_sources=not reconcile)
        case='exact-query-'+inspection_sha
        report.update(request_id=case,search_evidence_sha256=inspection_sha,fingerprint=fp_dict)
        pending_search=store.get('development_exact_search','pending')
        if reconcile:
            prior=store.get('development_exact_search',case,{})
            if (not isinstance(pending_search,dict) or pending_search.get('request')!=case or
                pending_search.get('fingerprint')!=fp_dict or pending_search.get('navigation_request')!=nav['request'] or
                pending_search.get('phase')!='terminal_unknown' or prior.get('setter_returned') is not True or
                prior.get('worker_exited') is not True):
                raise ConsoleError('exact_search_returned_unknown_required')
        elif pending_search or store.get('development_exact_search',case) is not None:
            raise ConsoleError('exact_search_prior_attempt_or_recovery_required')
        if any(store.get(k,'pending') for k in ('patch','thread_hook','draft_probe','development_window_restore')):
            raise ConsoleError('exact_search_other_recovery_required')
        native=NativeGate(fp_dict['hwnd']);fp=native.fingerprint()
        if asdict(fp)!=fp_dict:raise ConsoleError('exact_search_process_changed')
        baseline=snapshot(fp.pid);unchanged_readonly(baseline,baseline);tick=input_tick()
        observer=DesktopEventObserver(fp.pid).__enter__()
        def check():
            if (original.DIRECTORY/'STOP').exists():raise ConsoleError('exact_search_emergency_stop')
            if time.monotonic()-started>25:raise ConsoleError('exact_search_deadline')
            if native.fingerprint()!=fp or store.get('binding','account')!=binding:raise ConsoleError('exact_search_binding_changed')
            if store.get('development_navigation','pending')!=nav or store.get('development_navigation',nav['request'])!=attempt:
                raise ConsoleError('exact_search_navigation_changed')
            if lock_path.read_text(encoding='ascii')!=token:raise ConsoleError('exact_search_lock_changed')
            if input_tick()!=tick:raise ConsoleError('exact_search_human_takeover')
            if store.get('development_exact_search','pending')!=pending_search:raise ConsoleError('exact_search_journal_changed')
            if load_local_hashed(args.target_evidence)[1]!=evidence_sha or load_local_hashed(args.search_evidence)[1]!=inspection_sha:
                raise ConsoleError('exact_search_source_evidence_changed')
            scope.check(store);observer.check();unchanged_readonly(baseline,snapshot(fp.pid))
            return True
        gate=AccessibilityGate(native,allowed=True,save=lambda record:store.put('patch','pending',record))
        report['patch']=gate.enable(fp,check)
        context=mp.get_context('spawn');parent,child=context.Pipe()
        process=context.Process(target=worker,args=(child,fp_dict,args.private_label,args.private_id,runtime,reconcile))
        process.start();child.close()
        while True:
            check()
            if not parent.poll(.03):
                if not process.is_alive():raise ConsoleError('exact_search_worker_disconnected')
                continue
            item=parent.recv()
            if item.get('kind')=='checkpoint':parent.send({'continue':check()})
            elif item.get('kind')=='claim' and not claimed and not reconcile:
                check();claimed=True;report.update(state='unknown',action_executed=None)
                pending_search=dict(request=case,fingerprint=fp_dict,navigation_request=nav['request'],phase='set_value_unknown')
                store.put('development_exact_search',case,report)
                store.put('development_exact_search','pending',pending_search)
                observer.set_phase('set_exact_recipient_search');parent.send({'continue':True})
            elif item.get('ok') is True:
                operation=item['operation']
                if operation.get('action_executed') and not claimed:raise ConsoleError('exact_search_result_without_claim')
                report.update(operation,worker=item['result']);received=True;break
            else:raise ConsoleError(item.get('reason','exact_search_worker_result_invalid'))
        check()
    except Exception as exc:
        report.update(state='unknown' if claimed else 'failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'exact_search_coordinator_failed')
    finally:
        if parent:
            try:parent.send({'continue':False})
            except (OSError,EOFError):pass
        cleanup_resources(report,process=process,gate=gate,observer=observer,
            resources=(('parent',parent),('child',child),('native',native)))
        if report.get('events'):
            if not report['cleanup_errors']:report['state']='unknown' if claimed else 'failed'
            report['reason']='exact_search_focus_event'
        report['sources_unchanged']=sources()==report['sources']
        if not report['sources_unchanged']:report['state']='recovery_required';report['cleanup_errors'].append('exact_search_sources_changed')
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        try:
            if claimed:
                store.put('development_exact_search',case,report)
                known=report['state']=='exact_search_updated' or (received and report['state']=='failed' and report['action_executed'] is False)
                if known and not report['cleanup_errors']:store.put('development_exact_search','pending',None)
                else:
                    pending_search['phase']='terminal_unknown';store.put('development_exact_search','pending',pending_search)
            elif reconcile and report['state']=='exact_search_observed_after_unknown' and not report['cleanup_errors']:
                store.put('development_exact_search','pending',None)
        finally:store.close()
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--set-exact-recipient-search',action='store_true');mode.add_argument('--inspect-after-attempt',action='store_true')
    parser.add_argument('--allow-development-search-write',action='store_true');parser.add_argument('--allow-accessibility-patch',action='store_true')
    for name in ('account','private-id','private-label','target-evidence','context-evidence','search-evidence','synthetic-search-evidence'):
        parser.add_argument('--'+name)
    args=parser.parse_args(argv)
    if not (args.set_exact_recipient_search or args.inspect_after_attempt):
        print(json.dumps(dict(state='disabled',action_executed=False)));return 0
    try:report=run(args)
    except Exception as exc:report=dict(state='failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'exact_search_preparation_failed')
    path=original.DIRECTORY/('exact-recipient-search-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print(json.dumps(dict(path=str(path),state=report['state'],reason=report.get('reason'))))
    return 0 if report['state'] in ('exact_search_updated','already_exact_search','exact_search_observed_after_unknown') else 1


if __name__=='__main__':raise SystemExit(main())
