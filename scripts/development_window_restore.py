"""Opt-in development restoration of one bound HWND without requesting activation.

One public ShowWindowAsync request per journaled state transition, no UIA/client
methods, process memory writes, keys, clipboard, draft or send operations.
Never enabled by the production adapter. The current identity and baseline
remain mandatory; this does not infer an account from a window title.
"""
import argparse
import ctypes as c
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import re
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import probe_background_draft as original
from scripts.private_test_scope import PrivateTestScope
from wechat_agent_bridge.adapters.background import unchanged
from wechat_agent_bridge.console_models import ConsoleError

SW_SHOWMINNOACTIVE=7
SW_SHOWNOACTIVATE=4


def restore_transaction(port,checkpoint,claim,*,allowed=False,clock=time.monotonic,sleep=time.sleep,normal=False):
    attempted=False
    try:
        if not allowed: raise ConsoleError('development_window_state_opt_in_required')
        if type(normal) is not bool:raise ConsoleError('development_window_state_invalid_mode')
        checkpoint(); before=port.state()
        desired=dict(visible=True,iconic=not normal)
        if before==desired:
            return dict(state='already_normal' if normal else 'already_minimized',attempted=False,background_qualified=False)
        if normal and before!=dict(visible=True,iconic=True):
            raise ConsoleError('development_normal_requires_minimized_window')
        if not normal and before.get('visible') is not False:
            raise ConsoleError('development_restore_requires_hidden_window')
        claim(before)
        checkpoint()
        if port.state()!=before: raise ConsoleError('development_window_state_changed_before_request')
        attempted=True
        if not port.request(): raise ConsoleError('development_window_request_not_started')
        until=clock()+10
        while True:
            checkpoint()
            if port.state()==desired:
                checkpoint()
                return dict(state='shown_normal_for_development' if normal else 'restored_minimized_for_development',
                    attempted=True,background_qualified=False)
            if clock()>=until: raise ConsoleError('development_window_state_timeout')
            sleep(.05)
    except Exception as exc:
        return dict(state='unknown' if attempted else 'failed',attempted=attempted,background_qualified=False,
            reason=str(exc) if isinstance(exc,ConsoleError) else 'development_window_restore_failed')


class WindowPort:
    def __init__(self,hwnd,api=None,*,normal=False):
        if type(normal) is not bool:raise ConsoleError('development_window_state_invalid_mode')
        self.hwnd=hwnd
        self.command=SW_SHOWNOACTIVATE if normal else SW_SHOWMINNOACTIVE
        self.api=api or c.WinDLL('user32',use_last_error=True)
        for name in ('IsWindow','IsWindowVisible','IsIconic'):
            fn=getattr(self.api,name); fn.argtypes=[c.c_void_p];fn.restype=c.c_int
        self.api.ShowWindowAsync.argtypes=[c.c_void_p,c.c_int]
        self.api.ShowWindowAsync.restype=c.c_int
    def state(self):
        if not self.api.IsWindow(self.hwnd): raise ConsoleError('development_window_disappeared')
        return dict(visible=bool(self.api.IsWindowVisible(self.hwnd)),iconic=bool(self.api.IsIconic(self.hwnd)))
    def request(self): return bool(self.api.ShowWindowAsync(self.hwnd,self.command))


def sources():
    names=('scripts/development_window_restore.py','scripts/private_test_scope.py',
        'wechat_agent_bridge/adapters/accessibility_gate.py','wechat_agent_bridge/adapters/background.py')
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names}


def synthetic_sources():
    names=('scripts/development_window_restore.py','scripts/probe_window_restore.py','scripts/test_focus_etw.py')
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names}


def require_synthetic(path):
    report=load_local(path)
    cleanup=report.get('cleanup',{})
    if (report.get('kind')!='synthetic_window_restore' or report.get('passed') is not True or
        report.get('contains_real_client') is not False or report.get('sources')!=synthetic_sources() or
        report.get('sources_unchanged') is not True or report.get('os')!=platform.version() or
        report.get('result',{}).get('state')!='restored_minimized_for_development' or report.get('request_count')!=1 or
        report.get('checkpoint_count',0)<3 or
        report.get('normal_result',{}).get('state')!='shown_normal_for_development' or
        report.get('normal_request_count')!=1 or report.get('normal_visibility_verified') is not True or
        report.get('working_desktop_unchanged') is not True or report.get('local_focus_unchanged') is not True or
        report.get('requester_owner_match') is not True or report.get('requester_distinct_process') is not True or
        report.get('requester_focus_unchanged') is not True or cleanup.get('requester_exitcode')!=0 or
        cleanup.get('terminated') is not False or cleanup.get('child_exitcode')!=0 or
        any(cleanup.get(k) is not True for k in ('child_exited','window_destroyed','thread_desktop_restored',
            'child_desktop_closed','parent_desktop_closed','desktop_absent','requester_exited',
            'requester_thread_desktop_restored','requester_desktop_closed'))):
        raise ConsoleError('development_restore_synthetic_evidence_required')


def load_local(path):
    path=Path(path).resolve()
    if path.parent!=original.DIRECTORY.resolve() or path.stat().st_size>1000000:
        raise ConsoleError('development_restore_local_evidence_required')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def load_local_hashed(path):
    path=Path(path).resolve()
    if path.parent!=original.DIRECTORY.resolve() or path.stat().st_size>1000000:
        raise ConsoleError('development_restore_local_evidence_required')
    payload=path.read_bytes()
    if len(payload)>1000000:raise ConsoleError('development_restore_local_evidence_required')
    return json.loads(payload.decode('utf-8-sig')),hashlib.sha256(payload).hexdigest()


def cua_minimized_case(report,fingerprint,account,evidence_sha256):
    """Bind a new, observed CUA transition without reopening older cases.

    This receipt only prepares window state. It never verifies a recipient or
    grants reading/sending; PrivateTestScope and live checks remain mandatory.
    """
    if (not isinstance(account,str) or not account or not isinstance(fingerprint,dict) or
        not isinstance(report,dict) or report.get('kind')!='development_cua_window_ready' or
        report.get('binding_reference')!=fingerprint or report.get('main_window_id')!=fingerprint.get('hwnd') or
        report.get('final_state')!='minimized_confirmed_by_computer_use' or
        report.get('sender_visual_id_match') is not True or
        report.get('sender_id_sha256')!=hashlib.sha256(account.encode('utf-8')).hexdigest() or
        report.get('private_label_visible') is not True or
        report.get('private_draft_visible_empty_before_minimize') is not True or
        report.get('own_profile_collapsed') is not True or
        report.get('later_window_list_included_target') is not True or
        any(report.get(k) is not False for k in ('native_uia_used','send_invoked','api_called',
            'process_patch_applied','helper_loaded','account_binding_modified','background_qualified',
            'production_enabled','unrelated_conversation_intentionally_opened')) or
        any(type(report.get(k)) is not int or report[k]!=0 for k in ('launch_calls','text_input_calls')) or
        not isinstance(evidence_sha256,str) or not re.fullmatch(r'[a-f0-9]{64}',evidence_sha256)):
        raise ConsoleError('development_cua_minimized_receipt_required')
    preview=report.get('unexpected_preview',{})
    if (not isinstance(preview,dict) or (preview.get('opened') is not False and
            (preview.get('opened') is not True or preview.get('closed') is not True))):
        raise ConsoleError('development_cua_preview_cleanup_required')
    return 'normal-after-cua-'+str(fingerprint['created'])+'-'+evidence_sha256


def finalize_journal(store,lock,key,report,*,claimed,pending,inspecting):
    if claimed:
        store.put('development_window_trials',key,report)
        if report['state']=='unknown':
            journal=store.get('development_window_restore','pending')
            journal['phase']='terminal_unknown';store.put('development_window_restore','pending',journal)
        else:store.put('development_window_restore','pending',None)
    reconciled=report['state'] in ('minimized_observed_after_unknown','normal_observed_after_unknown') and not report['cleanup_errors']
    if reconciled:
        if lock.path.read_text(encoding='ascii')!=pending['lock_token']:
            raise ConsoleError('development_restore_recovery_lock_changed')
        lock.token=pending['lock_token'];lock.owned=True
    if report['state']!='unknown' and (not inspecting or reconciled):lock.release()
    if reconciled:store.put('development_window_restore','pending',None)


def run(args):
    ready_path=getattr(args,'cua_ready_evidence',None)
    after_cua=getattr(args,'prepare_after_cua_minimize',False) or (args.inspect_after_attempt and bool(ready_path))
    if not (args.allow_development_window_state and args.account and args.private_id and args.private_label and
            args.target_evidence and (ready_path if after_cua else args.cua_failure_evidence)):
        raise ConsoleError('development_restore_explicit_private_scope_required')
    if ready_path and not after_cua:raise ConsoleError('development_cua_receipt_mode_required')
    if not args.inspect_after_attempt:
        if not args.synthetic_evidence:raise ConsoleError('development_restore_synthetic_evidence_required')
        require_synthetic(args.synthetic_evidence)
    normal=after_cua or getattr(args,'show_normal_after_minimized',False) or (
        args.inspect_after_attempt and getattr(args,'normal_scope',False))
    if getattr(args,'normal_scope',False) and not args.inspect_after_attempt:
        raise ConsoleError('development_normal_scope_inspection_only')
    from wechat_agent_bridge.adapters.accessibility_gate import NativeGate
    from wechat_agent_bridge.adapters.background import snapshot
    from wechat_agent_bridge.adapters.desktop_events import DesktopEventObserver
    from wechat_agent_bridge.console_store import ConsoleStore
    from wechat_agent_bridge.protection import DPAPIProtector
    store=ConsoleStore(original.DIRECTORY/'gate.sqlite3',DPAPIProtector())
    lock=original.RunLock(original.DIRECTORY/'autofocus-draft.lock')
    native=observer=None; claimed=False; key=None; pending=None; started=time.monotonic()
    report=dict(kind='development_bound_window_restore',state='failed',attempted=False,
        background_qualified=False,process_modified=False,patch_enabled=False,helper_loaded=False,
        send_invoked=False,api_called=False,sources=sources(),cleanup_errors=[])
    try:
        binding=store.get('binding','account',{}); fp_dict=binding.get('fingerprint',{})
        scope=PrivateTestScope(args.private_id,args.private_label)
        scope.resolve(binding,load_local(args.target_evidence),args.account,fp_dict,store,original.DIRECTORY)
        report.update(scope.metadata())
        if after_cua:
            ready_path=Path(ready_path).resolve()
            ready,ready_sha=load_local_hashed(ready_path)
            key=cua_minimized_case(ready,fp_dict,args.account,ready_sha)
            report['cua_ready_evidence_sha256']=ready_sha
        else:
            failure=load_local(args.cua_failure_evidence)
            if (failure.get('kind')!='development_cua_existing_window_restore' or failure.get('state')!='app_not_exposed' or
                failure.get('binding_reference')!=fp_dict or failure.get('original_window_id')!=fp_dict.get('hwnd') or
                failure.get('launch_attempts')!=1 or failure.get('further_input_stopped') is not True):
                raise ConsoleError('development_restore_prior_cua_failure_required')
            key=('normal-private-' if normal else 'hidden-private-')+str(fp_dict['created'])
        report['request_id']=key
        pending=store.get('development_window_restore','pending')
        if not args.inspect_after_attempt:
            lock.acquire()
            if store.get('development_window_trials',key) is not None:
                raise ConsoleError('development_window_restore_already_attempted')
            if pending or any(store.get(k,'pending') for k in ('patch','thread_hook','draft_probe','development_navigation')):
                raise ConsoleError('development_restore_prior_recovery_required')
            if normal and not after_cua:
                previous=store.get('development_window_trials','hidden-private-'+str(fp_dict['created']),{})
                if (previous.get('state')!='restored_minimized_for_development' or previous.get('fingerprint')!=fp_dict or
                    previous.get('events_observed')!=0 or previous.get('cleanup_errors')!=[]):
                    raise ConsoleError('development_confirmed_minimized_preparation_required')
        elif (not isinstance(pending,dict) or pending.get('key')!=key or pending.get('fingerprint')!=fp_dict or
              pending.get('phase')!='terminal_unknown'):
            raise ConsoleError('development_restore_terminal_unknown_required')
        native=NativeGate(fp_dict['hwnd']);fp=native.fingerprint()
        if asdict(fp)!=fp_dict: raise ConsoleError('development_restore_process_changed')
        report['fingerprint']=fp_dict
        baseline=snapshot(fp.pid);unchanged(baseline,baseline)
        observer=DesktopEventObserver(fp.pid).__enter__()
        def check():
            if (original.DIRECTORY/'STOP').exists(): raise ConsoleError('development_restore_emergency_stop')
            if time.monotonic()-started>=20: raise ConsoleError('development_restore_deadline')
            if native.fingerprint()!=fp or store.get('binding','account')!=binding:
                raise ConsoleError('development_restore_identity_changed')
            if after_cua and hashlib.sha256(Path(ready_path).read_bytes()).hexdigest()!=ready_sha:
                raise ConsoleError('development_cua_receipt_changed')
            scope.check(store);observer.check();unchanged(baseline,snapshot(fp.pid))
        report['window_mode']='normal_after_cua_minimize' if after_cua else (
            'normal_after_minimized' if normal else 'hidden_to_minimized')
        port=WindowPort(fp.hwnd,normal=normal)
        def claim(before):
            nonlocal claimed
            report.update(state='unknown',attempted=True,window_before=before)
            journal=dict(key=key,fingerprint=fp_dict,lock_token=lock.token,phase='request_unknown')
            claimed=True
            store.put('development_window_restore','pending',journal)
            store.put('development_window_trials',key,report)
        if args.inspect_after_attempt:
            check()
            if port.state()!=dict(visible=True,iconic=not normal): raise ConsoleError('development_restore_state_still_unconfirmed')
            until=time.monotonic()+.5
            while time.monotonic()<until: check();time.sleep(.02)
            report.update(state='normal_observed_after_unknown' if normal else 'minimized_observed_after_unknown',
                original_attempt_preserved=True)
        else:
            report.update(restore_transaction(port,check,claim,allowed=True,normal=normal))
        report['window_after']=port.state()
    except Exception as exc:
        report.update(state='unknown' if claimed else 'failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'development_restore_coordinator_failed')
    finally:
        if observer:
            try: observer.close()
            except Exception: observer.evidence.failed=True
            report['events_observed']=len(observer.evidence.events)
            if observer.evidence.failed or observer.evidence.events:
                report.update(state='unknown' if claimed else 'failed',reason='development_restore_focus_event')
        if native:
            try:native.close()
            except Exception:report['cleanup_errors'].append('development_restore_handle_close_failed')
        report['sources_unchanged']=sources()==report['sources']
        if not report['sources_unchanged']: report['cleanup_errors'].append('development_restore_sources_changed')
        if report['cleanup_errors']:report['state']='unknown' if claimed else 'failed'
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        try:
            finalize_journal(store,lock,key,report,claimed=claimed,pending=pending,inspecting=args.inspect_after_attempt)
        finally:store.close()
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--restore-hidden-window',action='store_true')
    mode.add_argument('--show-normal-after-minimized',action='store_true')
    mode.add_argument('--prepare-after-cua-minimize',action='store_true')
    mode.add_argument('--inspect-after-attempt',action='store_true')
    parser.add_argument('--allow-development-window-state',action='store_true')
    parser.add_argument('--normal-scope',action='store_true',help='Read-only follow-up of the normal-window case.')
    for flag in ('account','private-id','private-label','target-evidence','cua-failure-evidence','cua-ready-evidence','synthetic-evidence'):parser.add_argument('--'+flag)
    args=parser.parse_args(argv)
    if not (args.restore_hidden_window or args.show_normal_after_minimized or args.prepare_after_cua_minimize or args.inspect_after_attempt):
        print(json.dumps(dict(state='disabled',send_invoked=False,api_called=False)));return 0
    try:report=run(args)
    except Exception as exc:report=dict(state='failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'development_restore_preparation_failed')
    path=original.DIRECTORY/('development-window-restore-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2)
    print(json.dumps(dict(path=str(path),state=report['state'],reason=report.get('reason'))))
    return 0 if report['state'] in ('already_minimized','already_normal','restored_minimized_for_development',
        'shown_normal_for_development','minimized_observed_after_unknown','normal_observed_after_unknown') else 1


if __name__=='__main__':raise SystemExit(main())
