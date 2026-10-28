"""Opt-in, read-only identity binding of the CUA-opened private contact card.

Uses the previously authorized single-byte accessibility switch and its existing
restore journal. This is separate from the button-only sending helper; this
module never imports or loads that helper or calls an internal client method.
May inspect an already-foreground card during development. An explicit extra
flag captures encrypted private history solely as an ineligible baseline.
Does not activate, select, type, send, upload, or grant console permissions.
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
from scripts.contact_profile_identity import inspect,validate_visual_evidence,unchanged_readonly,sha,private_baseline
from wechat_agent_bridge.console_models import ConsoleError


def sources():
    names=('scripts/verify_contact_profile.py','scripts/contact_profile_identity.py',
        'scripts/probe_bound_message_labels.py','wechat_agent_bridge/adapters/bound_read.py',
        'wechat_agent_bridge/adapters/windows_uia.py','wechat_agent_bridge/adapters/uia_client.py',
        'wechat_agent_bridge/adapters/accessibility_gate.py')
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names}


def baseline_key(fingerprint,expected):
    return sha(expected)+':'+str(fingerprint['created'])


def existing_epoch_baseline(store,binding,expected):
    """Inspect this sender's archived metadata, never the history capture."""
    from scripts.recover_exited_navigation import content_hash
    sender=binding.get('account',{}).get('wechat_id')
    if not sender: raise ConsoleError('private_baseline_lineage_invalid')
    seen=set()
    current=binding
    for _ in range(16):
        fp=current.get('fingerprint',{})
        account=current.get('account',{})
        if (account.get('wechat_id')!=sender or
            account.get('identity_source')!='uia_own_profile_exact_id_user_selected_window' or
            type(fp.get('created')) is not int or fp['created']<=0):
            raise ConsoleError('private_baseline_lineage_invalid')
        identity=content_hash(fp)
        if identity in seen: raise ConsoleError('private_baseline_lineage_invalid')
        seen.add(identity)
        key=baseline_key(fp,expected)
        baseline=store.get('private_baselines',key)
        if baseline is not None: return key,baseline
        previous=current.get('previous_fingerprint_sha256')
        if previous is None: return None
        archived=store.get('binding_epochs',previous)
        if not isinstance(archived,dict) or content_hash(archived.get('fingerprint',{}))!=previous:
            raise ConsoleError('private_baseline_lineage_invalid')
        current=archived
    raise ConsoleError('private_baseline_lineage_invalid')


def save_verified_result(store,expected,report):
    """Identity refresh must not replace an already captured history baseline."""
    if (report.get('state')!='contact_profile_verified' or report.get('patch_restored') is not True or
        report.get('worker_exited') is not True or report.get('events_observed')!=0 or
        report.get('sources_unchanged') is not True or report.get('cleanup_errors')!=[] or
        report.get('worker',{}).get('identity',{}).get('expected_id_sha256')!=sha(expected)):
        raise ConsoleError('contact_complete_result_required_before_save')
    baseline=report['worker'].get('private_baseline')
    if baseline is not None:
        if baseline.get('upload_eligible') is not False or baseline.get('reverified_before_save') is not True:
            raise ConsoleError('private_baseline_result_unverified')
        key=baseline_key(report['fingerprint'],expected)
        existing=store.get('private_baselines',key)
        if existing is not None and existing.get('worker',{}).get('private_baseline')!=baseline:
            raise ConsoleError('private_baseline_immutable')
        store.put('private_baselines',key,report)
    store.put('verified_contacts',sha(expected),report)


def worker(pipe,fp,label,expected,capture=False):
    sys.coinit_flags=0
    try:
        from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
        client=BackgroundUIAClient()
        root=client.element_from_handle(fp['hwnd'])
        if root.CurrentProcessId!=fp['pid'] or root.CurrentNativeWindowHandle!=fp['hwnd']:
            raise ConsoleError('contact_root_identity_changed')
        from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
        from wechat_agent_bridge.adapters.background import input_timestamp
        backend=WindowsUIA()
        tick=input_timestamp()
        def input_check():
            if input_timestamp()!=tick:raise ConsoleError('contact_human_takeover')
        value=inspect(backend,fp['pid'],fp['hwnd'],label,expected,checkpoint=input_check)
        if capture:
            from comtypes.gen import UIAutomationClient as uia
            from scripts.probe_bound_message_labels import collect_labels,protect_capture
            from wechat_agent_bridge.protection import DPAPIProtector
            baseline=private_baseline(value,
                lambda:collect_labels(client._client,root,uia,fp['pid']),
                lambda captured:protect_capture(captured,original.DIRECTORY,DPAPIProtector()),
                lambda:inspect(backend,fp['pid'],fp['hwnd'],label,expected))
            value.update(messages_read=True,private_baseline=baseline)
        client.require_background_mode()
        pipe.send(dict(ok=True,result=value))
    except Exception as exc:
        pipe.send(dict(ok=False,reason=str(exc) if isinstance(exc,ConsoleError) else 'contact_profile_worker_failed',
            target_metadata=getattr(exc,'target_metadata',None)))
    finally: pipe.close()


def run(args):
    if not (args.allow_accessibility_patch and args.account and args.private_id and args.private_label and
            args.cua_evidence and args.synthetic_evidence) or args.account==args.private_id:
        raise ConsoleError('contact_explicit_distinct_identity_scope_required')
    from scripts.probe_bound_uia_context import require_context_canary
    from wechat_agent_bridge.adapters.accessibility_gate import NativeGate,AccessibilityGate
    from wechat_agent_bridge.adapters.background import snapshot
    from wechat_agent_bridge.adapters.desktop_events import DesktopEventObserver
    from wechat_agent_bridge.console_store import ConsoleStore
    from wechat_agent_bridge.protection import DPAPIProtector
    require_context_canary(args.synthetic_evidence)
    path=Path(args.cua_evidence).resolve()
    if path.parent!=original.DIRECTORY.resolve() or path.stat().st_size>100000:
        raise ConsoleError('contact_local_visual_evidence_required')
    evidence=json.loads(path.read_text(encoding='utf-8-sig'))
    store=ConsoleStore(original.DIRECTORY/'gate.sqlite3',DPAPIProtector())
    native=gate=observer=process=parent=child=None
    lock=original.RunLock(original.DIRECTORY/'autofocus-draft.lock')
    report=dict(kind='bound_private_contact_identity',state='failed',sources=sources(),
        source_evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),cleanup_errors=[],
        send_invoked=False,api_called=False,messages_read=False,account_binding_modified=False,
        background_qualified=False,production_enabled=False)
    capture=getattr(args,'capture_private_baseline',False)
    report['private_baseline_requested']=capture
    report['message_read_attempted']=False
    if capture: report['messages_read']=None
    started=time.monotonic()
    try:
        lock.acquire()
        if any(store.get(k,'pending') for k in ('patch','thread_hook','draft_probe','development_navigation','development_window_restore')):
            raise ConsoleError('contact_prior_recovery_required')
        binding=store.get('binding','account',{})
        if (binding.get('account',{}).get('wechat_id')!=args.account or
            binding.get('account',{}).get('identity_source')!='uia_own_profile_exact_id_user_selected_window'):
            raise ConsoleError('contact_sender_binding_required')
        fp_dict=binding.get('fingerprint',{})
        validate_visual_evidence(evidence,fp_dict,args.private_id,args.private_label)
        if capture and existing_epoch_baseline(store,binding,args.private_id) is not None:
            raise ConsoleError('private_baseline_already_bound')
        native=NativeGate(fp_dict['hwnd']); fp=native.fingerprint()
        if asdict(fp)!=fp_dict: raise ConsoleError('contact_bound_process_changed')
        report['fingerprint']=fp_dict
        baseline=snapshot(fp.pid); unchanged_readonly(baseline,baseline)
        report['foreground_readonly']=baseline.target_active
        observer=DesktopEventObserver(fp.pid).__enter__()
        def guard():
            if (original.DIRECTORY/'STOP').exists(): raise ConsoleError('contact_emergency_stop')
            if time.monotonic()-started>25: raise ConsoleError('contact_readonly_timeout')
            if native.fingerprint()!=fp or store.get('binding','account')!=binding:
                raise ConsoleError('contact_sender_binding_changed')
            observer.check(); unchanged_readonly(baseline,snapshot(fp.pid))
            return True
        gate=AccessibilityGate(native,allowed=True,save=lambda record:store.put('patch','pending',record))
        report['patch']=gate.enable(fp,guard)
        context=mp.get_context('spawn'); parent,child=context.Pipe()
        process=context.Process(target=worker,args=(child,fp_dict,args.private_label,args.private_id,capture))
        report['message_read_attempted']=capture
        process.start(); child.close()
        while True:
            guard()
            if parent.poll(.05):
                value=parent.recv(); break
            if not process.is_alive(): raise ConsoleError('contact_worker_disconnected')
        guard()
        if value.get('ok') is not True:
            if value.get('target_metadata') is not None: report['target_metadata']=value['target_metadata']
            raise ConsoleError(value.get('reason','contact_worker_result_invalid'))
        result=value['result']
        if (result.get('state')!='contact_profile_verified' or
            result.get('identity',{}).get('expected_id_sha256')!=sha(args.private_id)):
            raise ConsoleError('contact_worker_identity_mismatch')
        report['worker']=result
        if capture and (result.get('private_baseline',{}).get('upload_eligible') is not False or
                        result.get('private_baseline',{}).get('reverified_before_save') is not True or
                        result.get('messages_read') is not True):
            raise ConsoleError('private_baseline_result_unverified')
        report['messages_read']=result.get('messages_read') is True
        report['state']='contact_profile_verified'
    except BaseException as exc:
        report['reason']=str(exc) if isinstance(exc,ConsoleError) else (
            'contact_operator_cancelled' if isinstance(exc,KeyboardInterrupt) else 'contact_profile_coordinator_failed')
    finally:
        if process and process.pid:
            process.join(1)
            if process.is_alive(): process.terminate(); process.join(2)
            report['worker_exited']=not process.is_alive()
            if process.exitcode!=0: report['cleanup_errors'].append('contact_worker_exit_unconfirmed')
        if gate:
            try:
                report['restore']=gate.restore()
                report['patch_restored']=report['restore']['state'] in ('restored','unchanged')
                if not report['patch_restored']: report['cleanup_errors'].append('contact_patch_restore_unconfirmed')
            except BaseException: report['cleanup_errors'].append('contact_patch_restore_failed')
        if observer:
            try: observer.close()
            except BaseException: observer.evidence.failed=True
            report['events_observed']=len(observer.evidence.events)
            if observer.evidence.failed or observer.evidence.events:
                report.update(state='failed',reason='contact_desktop_event_change')
        for label,resource in (('worker_process',process if report.get('worker_exited') else None),
                               ('native',native),('parent_pipe',parent),('child_pipe',child)):
            if resource:
                try: resource.close()
                except BaseException: report['cleanup_errors'].append('contact_'+label+'_close_failed')
        report['sources_unchanged']=sources()==report['sources']
        if not report['sources_unchanged']: report['cleanup_errors'].append('contact_sources_changed')
        if report['cleanup_errors']: report['state']='recovery_required'
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        try:
            if report['state']=='contact_profile_verified':
                save_verified_result(store,args.private_id,report)
            if not store.get('patch','pending'): lock.release()
        finally: store.close()
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--inspect-contact-profile',action='store_true')
    parser.add_argument('--allow-accessibility-patch',action='store_true')
    parser.add_argument('--capture-private-baseline',action='store_true')
    for flag in ('account','private-id','private-label','cua-evidence','synthetic-evidence'):
        parser.add_argument('--'+flag)
    args=parser.parse_args(argv)
    if not args.inspect_contact_profile:
        print(json.dumps(dict(state='disabled',send_invoked=False,api_called=False))); return 0
    try: report=run(args)
    except Exception as exc:
        report=dict(state='failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'contact_profile_preparation_failed',
            send_invoked=False,api_called=False)
    path=original.DIRECTORY/('contact-profile-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as stream: json.dump(report,stream,indent=2)
    print(json.dumps(dict(path=str(path),state=report['state'],reason=report.get('reason'),patch_restored=report.get('patch_restored'))))
    return 0 if report['state']=='contact_profile_verified' else 1


if __name__=='__main__': raise SystemExit(main())
