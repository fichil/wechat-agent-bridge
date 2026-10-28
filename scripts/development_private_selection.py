"""Opt-in semantic selection of one exact-ID-verified private test row.

No input text, draft cleanup, send, clipboard or internal client call. This is
development preparation, never a production background capability. A live
Select requires owned-control evidence and cannot repeat an unknown action.
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

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import probe_background_draft as original
from scripts.development_navigation import input_tick,cleanup_resources
from scripts.development_window_restore import load_local_hashed,cua_minimized_case
from scripts.private_test_scope import PrivateTestScope
from scripts.contact_profile_identity import inspect_recovery_card,sha
from wechat_agent_bridge.console_models import ConsoleError


def select_transaction(port,checkpoint,claim,*,allowed=False):
    attempted=False
    try:
        if not allowed:raise ConsoleError('private_selection_development_opt_in_required')
        checkpoint();port.verify_row()
        if port.selected():
            port.verify_target()
            return dict(state='already_selected',attempted=False,background_qualified=False)
        claim();checkpoint();port.verify_row()
        if port.selected():raise ConsoleError('private_selection_changed_before_call')
        attempted=True
        port.select()
        checkpoint();port.verify_row()
        if not port.selected():raise ConsoleError('private_selection_readback_missing')
        port.verify_target();checkpoint()
        return dict(state='selected_for_development',attempted=True,background_qualified=False)
    except Exception as exc:
        return dict(state='unknown' if attempted else 'failed',attempted=attempted,background_qualified=False,
            reason=str(exc) if isinstance(exc,ConsoleError) else 'private_selection_operation_failed')


def sources():
    paths=('scripts/development_private_selection.py','scripts/probe_private_selection.py',
        'scripts/private_test_scope.py','scripts/contact_profile_identity.py','scripts/development_navigation.py',
        'scripts/probe_uia_autofocus.py','scripts/test_focus_etw.py',
        'wechat_agent_bridge/adapters/uia_client.py','wechat_agent_bridge/adapters/windows_uia.py')
    return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}


def require_synthetic(path):
    value,_digest=load_local_hashed(path)
    if (value.get('kind')!='owned_private_selection_probe' or value.get('passed') is not True or
        value.get('contains_real_client') is not False or value.get('sources')!=sources() or
        value.get('os')!=platform.version() or value.get('working_desktop_unchanged') is not True or
        value.get('sources_unchanged') is not True or value.get('operation',{}).get('select_calls')!=1 or
        value.get('operation',{}).get('selection_readback') is not True or
        value.get('native',{}).get('selected_index')!=1 or value.get('cleanup',{}).get('terminated') is not False or
        value.get('uia_dll_unchanged') is not True or value.get('mta_verified') is not True or
        value.get('cleanup',{}).get('errors')!=[] or
        any(value.get('cleanup',{}).get(k) is not True for k in (
            'child_exited','native_cleanup_confirmed','owned_windows_absent','desktop_closed','desktop_absent'))):
        raise ConsoleError('private_selection_owned_recovery_required')


def require_recovery_result(report,expected,recipient):
    identity=report.get('current_contact_identity',{})
    if (report.get('state')!='selection_observed_after_unknown' or report.get('attempted') is not False or
        report.get('recipient_exact_id_reverified') is not True or
        report.get('identity_source')!='current_uia_contact_card_during_selection_recovery' or
        report.get('cleanup_errors')!=[] or report.get('events')!=[] or report.get('patch_restored') is not True or
        report.get('worker_exited') is not True or report.get('sources_unchanged') is not True or
        not isinstance(identity,dict) or identity.get('exact_id_verified') is not True or
        identity.get('expected_id_sha256')!=sha(recipient)):
        raise ConsoleError('private_selection_current_identity_and_clean_recovery_required')
    current=report.get('current_target_controls',{})
    rid=identity.get('profile_runtime_id')
    if (not isinstance(rid,list) or not 1<=len(rid)<=16 or any(type(x) is not int for x in rid) or
        type(identity.get('profile_hwnd')) is not int or identity['profile_hwnd']<=0 or
        identity['profile_hwnd']==report.get('fingerprint',{}).get('hwnd')):
        raise ConsoleError('private_selection_current_profile_identity_required')
    original.check_draft_target(expected,current)
    if current.get('private_header_runtime_id')!=expected.get('private_header_runtime_id'):
        raise ConsoleError('private_selection_recovery_header_changed')


def worker(pipe,fp,label,expected,inspect_only=False,recipient=None):
    sys.coinit_flags=0
    try:
        from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
        client=BackgroundUIAClient();root=client.element_from_handle(fp['hwnd'])
        from comtypes.gen import UIAutomationClient as uia
        from scripts.probe_bound_semantics import unique_by_runtime
        from scripts.contact_profile_identity import require_target
        from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
        row=unique_by_runtime(root,client._client,uia,uia.UIA_ListItemControlTypeId,expected['session_runtime_id'])
        pattern=row.GetCurrentPattern(uia.UIA_SelectionItemPatternId).QueryInterface(uia.IUIAutomationSelectionItemPattern)
        backend=WindowsUIA()
        class Port:
            def verify_row(self):
                client.require_background_mode()
                if (root.CurrentProcessId!=fp['pid'] or root.CurrentNativeWindowHandle!=fp['hwnd'] or
                    row.CurrentProcessId!=fp['pid'] or tuple(row.GetRuntimeId())!=tuple(expected['session_runtime_id']) or
                    row.CurrentClassName!='mmui::ChatSessionCell' or
                    row.CurrentAutomationId!='session_item_'+label or not row.CurrentIsEnabled):
                    raise ConsoleError('private_selection_bound_row_changed')
            def selected(self):
                value=pattern.CurrentIsSelected
                if type(value) not in (bool,int) or value not in (0,1):
                    raise ConsoleError('private_selection_state_unknown')
                return bool(value)
            def verify_target(self):
                _root,current=require_target(backend,fp['pid'],fp['hwnd'],label)
                original.check_draft_target(expected,current)
            def select(self):pattern.Select()
        port=Port()
        if inspect_only:
            port.verify_row()
            if not port.selected():raise ConsoleError('private_selection_still_unconfirmed')
            def checkpoint():
                original._exchange(pipe,'checkpoint','private_selection_readonly_recovery')
                port.verify_row()
                if not port.selected():raise ConsoleError('private_selection_still_unconfirmed')
            identity=inspect_recovery_card(backend,fp['pid'],fp['hwnd'],label,recipient,expected,checkpoint)
            result=dict(state='selection_observed_after_unknown',attempted=False,background_qualified=False,**identity)
        else:
            result=select_transaction(port,lambda:original._exchange(pipe,'checkpoint','private_selection'),
                lambda:original._exchange(pipe,'claim','verified_private_SelectionItem_Select'),allowed=True)
        pipe.send(dict(kind='result',value=result))
    except Exception as exc:
        pipe.send(dict(kind='error',value=str(exc) if isinstance(exc,ConsoleError) else 'private_selection_worker_failed'))
    finally:pipe.close()


def run(args):
    if not (args.development_foreground and args.allow_accessibility_patch and args.account and args.private_id and
        args.private_label and args.target_evidence and args.cua_ready_evidence and args.context_evidence):
        raise ConsoleError('private_selection_exact_scope_required')
    if not args.inspect_after_attempt:
        if not args.selection_evidence:raise ConsoleError('private_selection_owned_recovery_required')
        require_synthetic(args.selection_evidence)
    from scripts.probe_bound_uia_context import require_context_canary
    require_context_canary(args.context_evidence)
    from wechat_agent_bridge.adapters.accessibility_gate import NativeGate,AccessibilityGate
    from wechat_agent_bridge.adapters.background import snapshot
    from wechat_agent_bridge.adapters.desktop_events import DesktopEventObserver
    from wechat_agent_bridge.console_store import ConsoleStore
    from wechat_agent_bridge.protection import DPAPIProtector
    from wechat_agent_bridge.adapters.windows_uia import VERIFIED_READ_PROFILES,VERIFIED_SEND_PROFILES
    evidence,evidence_sha=load_local_hashed(args.target_evidence)
    ready,ready_sha=load_local_hashed(args.cua_ready_evidence)
    store=ConsoleStore(original.DIRECTORY/'gate.sqlite3',DPAPIProtector())
    lock=original.RunLock(original.DIRECTORY/'autofocus-draft.lock')
    native=gate=observer=process=parent=child=None
    claimed=False;pending=None;request=None;received_result=False;started=time.monotonic()
    report=dict(kind='development_private_semantic_selection',state='failed',attempted=False,background_qualified=False,
        send_invoked=False,api_called=False,draft_written=False,clipboard_written=False,messages_read=False,
        sources=sources(),cleanup_errors=[],recipient_exact_id_reverified=False)
    try:
        binding=store.get('binding','account',{});fp_dict=binding.get('fingerprint',{})
        scope=PrivateTestScope(args.private_id,args.private_label)
        expected=scope.resolve(binding,evidence,args.account,fp_dict,store,original.DIRECTORY)
        report.update(scope.metadata())
        cua_minimized_case(ready,fp_dict,args.account,ready_sha)
        request='development-select-private-'+hashlib.sha256((evidence_sha+ready_sha).encode()).hexdigest()
        report.update(request_id=request,target_evidence_sha256=evidence_sha,cua_ready_evidence_sha256=ready_sha)
        pending=store.get('development_navigation','pending')
        if args.inspect_after_attempt:
            if (not isinstance(pending,dict) or pending.get('request')!=request or pending.get('fingerprint')!=fp_dict or
                pending.get('method')!='verified_private_SelectionItem_Select' or pending.get('phase')!='terminal_unknown'):
                raise ConsoleError('private_selection_terminal_unknown_required')
            from scripts.inspect_bound_search import require_terminal_selection
            original_attempt=store.get('development_navigation',request)
            require_terminal_selection(pending,original_attempt,fp_dict,lock.path.read_text(encoding='ascii'))
            if any(store.get(k,'pending') for k in ('patch','thread_hook','draft_probe','development_window_restore','development_exact_search')):
                raise ConsoleError('private_selection_prior_recovery_required')
        else:
            lock.acquire()
            if store.get('development_navigation',request) is not None:
                raise ConsoleError('private_selection_already_attempted')
            if pending or any(store.get(k,'pending') for k in ('patch','thread_hook','draft_probe','development_window_restore','development_exact_search')):
                raise ConsoleError('private_selection_prior_recovery_required')
        native=NativeGate(fp_dict['hwnd']);fp=native.fingerprint()
        if asdict(fp)!=fp_dict:raise ConsoleError('private_selection_process_changed')
        if fp.version in VERIFIED_READ_PROFILES or fp.version in VERIFIED_SEND_PROFILES:
            raise ConsoleError('private_selection_production_adapter_must_be_disabled')
        report['fingerprint']=fp_dict
        baseline=snapshot(fp.pid);tick=input_tick()
        if baseline.locked:raise ConsoleError('desktop_locked_or_unavailable')
        observer=DesktopEventObserver(fp.pid).__enter__()
        def check():
            if (original.DIRECTORY/'STOP').exists():raise ConsoleError('private_selection_emergency_stop')
            if time.monotonic()-started>25:raise ConsoleError('private_selection_deadline')
            if native.fingerprint()!=fp or store.get('binding','account')!=binding:
                raise ConsoleError('private_selection_binding_changed')
            if args.inspect_after_attempt and (store.get('development_navigation','pending')!=pending or
                store.get('development_navigation',request)!=original_attempt or
                lock.path.read_text(encoding='ascii')!=pending['lock_token']):
                raise ConsoleError('private_selection_original_recovery_changed')
            scope.check(store)
            if load_local_hashed(args.target_evidence)[1]!=evidence_sha or load_local_hashed(args.cua_ready_evidence)[1]!=ready_sha:
                raise ConsoleError('private_selection_source_changed')
            now=snapshot(fp.pid)
            if now.locked:raise ConsoleError('desktop_locked_or_unavailable')
            if input_tick()!=tick:raise ConsoleError('private_selection_human_takeover')
            if now.clipboard_sequence!=baseline.clipboard_sequence:raise ConsoleError('private_selection_clipboard_changed')
            if observer.evidence.failed:raise ConsoleError('private_selection_observer_failed')
            if not claimed and (now.foreground,now.focus)!=(baseline.foreground,baseline.focus):
                raise ConsoleError('private_selection_focus_changed_before_claim')
            return True
        gate=AccessibilityGate(native,allowed=True,save=lambda record:store.put('patch','pending',record))
        report['patch']=gate.enable(fp,check)
        context=mp.get_context('spawn');parent,child=context.Pipe()
        process=context.Process(target=worker,args=(child,fp_dict,args.private_label,expected,args.inspect_after_attempt,args.private_id))
        process.start();child.close()
        while True:
            check()
            if not parent.poll(.03):
                if not process.is_alive():raise ConsoleError('private_selection_worker_exited')
                continue
            item=parent.recv();kind=item.get('kind')
            if kind=='checkpoint':parent.send({'continue':check()})
            elif kind=='claim' and not claimed and not args.inspect_after_attempt:
                check();claimed=True;report.update(state='unknown',attempted=True)
                pending=dict(request=request,fingerprint=fp_dict,method='verified_private_SelectionItem_Select',
                    phase='request_unknown',lock_token=lock.token)
                store.put('development_navigation','pending',pending)
                store.put('development_navigation',request,report)
                observer.set_phase('explicit_development_private_selection');parent.send({'continue':True})
            elif kind=='result':
                result=item['value']
                if result.get('attempted') and not claimed:raise ConsoleError('private_selection_result_without_claim')
                report.update(result);received_result=True;break
            elif kind=='error':raise ConsoleError(item['value'])
            else:raise ConsoleError('private_selection_protocol_invalid')
        check()
    except Exception as exc:
        report.update(state='unknown' if claimed else 'failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'private_selection_coordinator_failed')
    finally:
        if parent:
            try:parent.send({'continue':False})
            except (OSError,EOFError):pass
        cleanup_resources(report,process=process,gate=gate,observer=observer,
            resources=(('parent',parent),('child',child),('native',native)))
        report['sources_unchanged']=sources()==report['sources']
        if not report['sources_unchanged']:report['cleanup_errors'].append('private_selection_sources_changed');report['state']='recovery_required'
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        try:
            reconciled=False
            if args.inspect_after_attempt and report['state']=='selection_observed_after_unknown':
                try:
                    require_recovery_result(report,expected,args.private_id)
                    reconciled=True
                except ConsoleError as exc:
                    report.update(state='failed',reason=str(exc))
            if reconciled:
                if lock.path.read_text(encoding='ascii')!=pending['lock_token']:raise ConsoleError('private_selection_recovery_lock_changed')
                # Persist the new observation before clearing pending. The old
                # request remains unknown and is never overwritten or replayed.
                store.put('development_navigation',request+':recovery-observed',report)
                lock.token=pending['lock_token'];lock.owned=True
            if not args.inspect_after_attempt and request is not None and claimed:
                store.put('development_navigation',request,report)
                if not report['cleanup_errors'] and (report['state']=='selected_for_development' or
                    (received_result and report['state']=='failed' and report['attempted'] is False)):
                    store.put('development_navigation','pending',None)
                else:
                    pending['phase']='terminal_unknown';store.put('development_navigation','pending',pending)
            if reconciled:store.put('development_navigation','pending',None)
            if not store.get('patch','pending') and ((not claimed and not args.inspect_after_attempt) or
                not store.get('development_navigation','pending')):lock.release()
        finally:store.close()
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--select-verified-private',action='store_true');mode.add_argument('--inspect-after-attempt',action='store_true')
    parser.add_argument('--development-foreground',action='store_true');parser.add_argument('--allow-accessibility-patch',action='store_true')
    for name in ('account','private-id','private-label','target-evidence','cua-ready-evidence','context-evidence','selection-evidence'):
        parser.add_argument('--'+name)
    args=parser.parse_args(argv)
    if not (args.select_verified_private or args.inspect_after_attempt):
        print(json.dumps(dict(state='disabled',send_invoked=False,api_called=False)));return 0
    try:report=run(args)
    except Exception as exc:report=dict(state='failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'private_selection_preparation_failed')
    import uuid
    path=original.DIRECTORY/('development-private-selection-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print(json.dumps(dict(path=str(path),state=report['state'],reason=report.get('reason'))))
    return 0 if report['state'] in ('selected_for_development','already_selected','selection_observed_after_unknown') else 1


if __name__=='__main__':raise SystemExit(main())
