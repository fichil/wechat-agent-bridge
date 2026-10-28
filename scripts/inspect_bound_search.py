"""Read only one bound main-window search Edit; never capture a chat surface.

An explicit terminal selection-unknown receipt permits this recovery inspection
under its existing lock. It cannot select, type, send, upload or release that
lock. Only query classification and control metadata leave the worker.
"""
import argparse
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
from scripts import probe_background_draft as original
from scripts.development_window_restore import load_local_hashed
from scripts.private_test_scope import PrivateTestScope
from scripts.contact_profile_identity import unchanged_readonly
from scripts.development_navigation import cleanup_resources
from wechat_agent_bridge.console_models import ConsoleError

SEARCH_CLASS='mmui::XValidatorTextEdit'


def inspect_control(root,raw,uia,fp,label,recipient):
    """Filter without Name/Text, then read Value from only the unique search.

    A caller-supplied class, text selector, arbitrary PID or coordinate is not
    available. In particular chat_input_field must never be read here.
    """
    if (root.CurrentProcessId!=fp['pid'] or root.CurrentNativeWindowHandle!=fp['hwnd'] or
        root.CurrentClassName!='mmui::MainWindow'):
        raise ConsoleError('search_root_identity_changed')
    condition=raw.CreateAndCondition(
        raw.CreatePropertyCondition(uia.UIA_ClassNamePropertyId,SEARCH_CLASS),
        raw.CreatePropertyCondition(uia.UIA_ControlTypePropertyId,uia.UIA_EditControlTypeId))
    matches=root.FindAll(uia.TreeScope_Descendants,condition)
    if type(matches.Length) is not int or matches.Length!=1:raise ConsoleError('search_control_ambiguous')
    node=matches.GetElement(0);runtime=tuple(node.GetRuntimeId())
    def verify():
        if (not runtime or len(runtime)>16 or any(type(x) is not int for x in runtime) or
            root.CurrentProcessId!=fp['pid'] or root.CurrentNativeWindowHandle!=fp['hwnd'] or
            root.CurrentClassName!='mmui::MainWindow' or
            node.CurrentProcessId!=fp['pid'] or node.CurrentClassName!=SEARCH_CLASS or
            node.CurrentControlType!=uia.UIA_EditControlTypeId or node.CurrentAutomationId!='' or
            tuple(node.GetRuntimeId())!=runtime):
            raise ConsoleError('search_control_identity_changed')
    verify()
    pattern=node.GetCurrentPattern(uia.UIA_ValuePatternId).QueryInterface(uia.IUIAutomationValuePattern)
    value=pattern.CurrentValue
    if not isinstance(value,str) or len(value)>128:raise ConsoleError('search_query_unexpected_or_too_long')
    writable=pattern.CurrentIsReadOnly
    if type(writable) not in (bool,int) or writable not in (0,1):raise ConsoleError('search_readonly_state_unknown')
    kind='recipient_id' if value==recipient else 'display_label' if value==label else 'empty' if not value else 'other'
    verify()
    return dict(search_runtime_id=list(runtime),search_class=SEARCH_CLASS,query_kind=kind,query_length=len(value),
        readonly=bool(writable),message_text_read=False,chat_draft_read=False,action_executed=False,
        write_capability_verified=False)


def require_terminal_selection(pending,attempt,fingerprint,lock_token):
    if (not isinstance(pending,dict) or pending.get('fingerprint')!=fingerprint or
        pending.get('method')!='verified_private_SelectionItem_Select' or pending.get('phase')!='terminal_unknown' or
        not isinstance(pending.get('request'),str) or not isinstance(pending.get('lock_token'),str) or
        len(pending['lock_token'])!=32 or any(c not in '0123456789abcdef' for c in pending['lock_token']) or
        pending.get('lock_token')!=lock_token or not isinstance(attempt,dict) or
        attempt.get('kind')!='development_private_semantic_selection' or
        attempt.get('request_id')!=pending['request'] or attempt.get('fingerprint')!=fingerprint or
        attempt.get('state')!='unknown' or attempt.get('worker_exited') is not True or
        attempt.get('patch_restored') is not True or attempt.get('cleanup_errors')!=[] or
        attempt.get('send_invoked') is not False or attempt.get('draft_written') is not False):
        raise ConsoleError('search_terminal_selection_lock_required')


def sources():
    names=('scripts/inspect_bound_search.py','scripts/private_test_scope.py','scripts/contact_profile_identity.py',
        'scripts/development_navigation.py','wechat_agent_bridge/adapters/uia_client.py',
        'wechat_agent_bridge/adapters/accessibility_gate.py')
    return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names}


def worker(pipe,fp,label,recipient):
    sys.coinit_flags=0
    try:
        from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
        client=BackgroundUIAClient();root=client.element_from_handle(fp['hwnd'])
        from comtypes.gen import UIAutomationClient as uia
        result=inspect_control(root,client._client,uia,fp,label,recipient)
        client.require_background_mode()
        pipe.send(dict(ok=True,result=result))
    except Exception as exc:
        pipe.send(dict(ok=False,reason=str(exc) if isinstance(exc,ConsoleError) else 'search_readonly_worker_failed'))
    finally:pipe.close()


def run(args,*,probe_worker=None,source_provider=None,report_kind='bound_search_control_readonly',
        success_state='search_control_inspected'):
    # Trusted internal composition for other strictly read-only search probes.
    # The public CLI exposes only this module's original single-control worker.
    source_provider=source_provider or sources
    if not (args.allow_accessibility_patch and args.account and args.private_id and args.private_label and
            args.target_evidence and args.context_evidence):raise ConsoleError('search_explicit_private_scope_required')
    from scripts.probe_bound_uia_context import require_context_canary
    require_context_canary(args.context_evidence)
    from wechat_agent_bridge.adapters.accessibility_gate import NativeGate,AccessibilityGate
    from wechat_agent_bridge.adapters.background import snapshot
    from wechat_agent_bridge.adapters.desktop_events import DesktopEventObserver
    from wechat_agent_bridge.console_store import ConsoleStore
    from wechat_agent_bridge.protection import DPAPIProtector
    evidence,evidence_sha=load_local_hashed(args.target_evidence)
    store=ConsoleStore(original.DIRECTORY/'gate.sqlite3',DPAPIProtector())
    native=gate=observer=process=parent=child=None;started=time.monotonic()
    report=dict(kind=report_kind,state='failed',sources=source_provider(),cleanup_errors=[],
        send_invoked=False,api_called=False,chat_draft_read=False,message_text_read=False,
        screenshot_captured=False,action_executed=False,lock_released=False,background_qualified=False)
    try:
        binding=store.get('binding','account',{});fp_dict=binding.get('fingerprint',{})
        scope=PrivateTestScope(args.private_id,args.private_label)
        scope.resolve(binding,evidence,args.account,fp_dict,store,original.DIRECTORY);report.update(scope.metadata())
        pending=store.get('development_navigation','pending',{})
        attempt=store.get('development_navigation',pending.get('request','')) if isinstance(pending,dict) else None
        lock_path=original.DIRECTORY/'autofocus-draft.lock'
        token=lock_path.read_text(encoding='ascii')
        require_terminal_selection(pending,attempt,fp_dict,token)
        if any(store.get(k,'pending') for k in ('patch','thread_hook','draft_probe','development_window_restore')):
            raise ConsoleError('search_prior_recovery_required')
        native=NativeGate(fp_dict['hwnd']);fp=native.fingerprint()
        if asdict(fp)!=fp_dict:raise ConsoleError('search_bound_process_changed')
        report['fingerprint']=fp_dict;baseline=snapshot(fp.pid);unchanged_readonly(baseline,baseline)
        observer=DesktopEventObserver(fp.pid).__enter__()
        def check():
            if (original.DIRECTORY/'STOP').exists():raise ConsoleError('search_emergency_stop')
            if time.monotonic()-started>20:raise ConsoleError('search_readonly_deadline')
            if source_provider()!=report['sources']:raise ConsoleError('search_sources_changed')
            if native.fingerprint()!=fp or store.get('binding','account')!=binding:raise ConsoleError('search_binding_changed')
            if store.get('development_navigation','pending')!=pending or lock_path.read_text(encoding='ascii')!=token:
                raise ConsoleError('search_recovery_lock_changed')
            if store.get('development_navigation',pending['request'])!=attempt:
                raise ConsoleError('search_original_attempt_changed')
            if load_local_hashed(args.target_evidence)[1]!=evidence_sha:raise ConsoleError('search_evidence_changed')
            scope.check(store);observer.check();unchanged_readonly(baseline,snapshot(fp.pid))
            return True
        gate=AccessibilityGate(native,allowed=True,save=lambda record:store.put('patch','pending',record))
        report['patch']=gate.enable(fp,check)
        context=mp.get_context('spawn');parent,child=context.Pipe()
        process=context.Process(target=probe_worker or worker,args=(child,fp_dict,args.private_label,args.private_id))
        process.start();child.close()
        while True:
            check()
            if parent.poll(.03):
                result=parent.recv();break
            if not process.is_alive():raise ConsoleError('search_worker_disconnected')
        check()
        if result.get('ok') is not True:raise ConsoleError(result.get('reason','search_worker_result_invalid'))
        report.update(state=success_state,worker=result['result'],selection_unknown_preserved=True)
    except Exception as exc:
        report['reason']=str(exc) if isinstance(exc,ConsoleError) else 'search_readonly_coordinator_failed'
    finally:
        cleanup_resources(report,process=process,gate=gate,observer=observer,
            resources=(('parent',parent),('child',child),('native',native)))
        if report.get('events'):
            if not report['cleanup_errors']:report['state']='failed'
            report['reason']='search_readonly_focus_event'
        report['sources_unchanged']=source_provider()==report['sources']
        if not report['sources_unchanged']:report['state']='recovery_required';report['cleanup_errors'].append('search_sources_changed')
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        store.close()
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--inspect-search-only',action='store_true');parser.add_argument('--allow-accessibility-patch',action='store_true')
    for name in ('account','private-id','private-label','target-evidence','context-evidence'):parser.add_argument('--'+name)
    args=parser.parse_args(argv)
    if not args.inspect_search_only:print(json.dumps(dict(state='disabled',action_executed=False)));return 0
    try:report=run(args)
    except Exception as exc:report=dict(state='failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'search_preparation_failed')
    path=original.DIRECTORY/('bound-search-control-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print(json.dumps(dict(path=str(path),state=report['state'],reason=report.get('reason'))))
    return 0 if report['state']=='search_control_inspected' else 1


if __name__=='__main__':raise SystemExit(main())
