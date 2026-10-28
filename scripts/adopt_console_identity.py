"""Adopt a restored native console identity; never reads chats or sends.

Unlike the background-only binder, enrollment permits the operator's foreground
card-opening step. That provenance is explicit and grants no background ability.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.console_store import digest


def validate(receipt,account,expected,source_manifest):
    from wechat_agent_bridge.adapters.accessibility_gate import Fingerprint,VERSION,DLL_SHA256,FLAG_RVA
    try:fp=Fingerprint(**receipt['fingerprint'])
    except (KeyError,TypeError):raise ConsoleError('console_adoption_receipt_invalid') from None
    if (receipt.get('kind')!='console_identity_integration' or receipt.get('state')!='verified' or
            receipt.get('cleanup_errors')!=[] or receipt.get('sources')!=source_manifest or not source_manifest or
            receipt.get('account_sha256')!=hashlib.sha256(expected.encode()).hexdigest() or
            fp.version!=VERSION or fp.dll_sha256!=DLL_SHA256 or fp.flag_rva!=FLAG_RVA or
            any(type(v) is not int or v<=0 for v in (fp.pid,fp.hwnd,fp.created,fp.module_base,fp.module_size)) or
            fp.module_size<=fp.flag_rva or type(receipt.get('restored_byte')) is not int or receipt['restored_byte']!=0 or
            type(receipt.get('worker_exit_code')) is not int):
        raise ConsoleError('console_adoption_receipt_invalid')
    for key in ('console_paused','fingerprint_unchanged','worker_started','worker_exited',
                'original_ciphertexts_unchanged','quota_file_unchanged','patch_pending_clear','sources_unchanged'):
        if receipt.get(key) is not True:raise ConsoleError('console_adoption_completion_unproven')
    for key in ('history_read','draft_read','send_invoked','api_called','acceptance_binding_modified',
                'background_qualified','chat_scope_bound'):
        if receipt.get(key) is not False:raise ConsoleError('console_adoption_scope_invalid')
    probe=receipt.get('identity_probe',{})
    if (probe.get('state')!='verified' or probe.get('identity_saved') is not True or
            probe.get('restored') is not True or probe.get('reason') is not None or
            probe.get('instance_id')!=f'{fp.pid}:{fp.hwnd}' or receipt.get('usage_before')!=receipt.get('usage_after') or
            not isinstance(receipt.get('usage_before'),dict)):
        raise ConsoleError('console_adoption_completion_unproven')
    if (not isinstance(account,dict) or account.get('wechat_id')!=expected or
            account.get('instance_id')!=f'{fp.pid}:{fp.hwnd}' or account.get('fingerprint')!=asdict(fp) or
            account.get('identity_source')!='uia_own_profile_exact_id_user_selected_window' or
            account.get('identity_certain') is not True or account.get('connected') is not True or
            not isinstance(account.get('id'),str) or not account['id']):
        raise ConsoleError('console_adoption_stored_identity_invalid')
    return fp


def require_old_epoch(old,account,fp):
    from scripts.rebind_sender_profile import SOURCE
    from scripts.recover_exited_navigation import content_hash
    if (old.get('account',{}).get('wechat_id')!=account['wechat_id'] or
            old['account'].get('id')!=account['id'] or old['account'].get('identity_source')!=SOURCE or
            not old.get('fingerprint') or old['fingerprint']==asdict(fp)):
        raise ConsoleError('console_adoption_old_epoch_invalid')
    return content_hash(old['fingerprint'])


def commit(store,old,account,fp,receipt_sha,pending,check):
    from scripts.rebind_sender_profile import PENDING
    old_key=require_old_epoch(old,account,fp)
    binding=dict(account=account,fingerprint=asdict(fp),previous_fingerprint_sha256=old_key,
                 requires_fresh_target_identity=True,identity_receipt_sha256=receipt_sha,
                 identity_workflow='console_identity_probe',background_qualified=False)
    completed=dict(state='sender_rebound',fingerprint=asdict(fp),identity_receipt_sha256=receipt_sha,
                   account_binding_modified=True,identity_workflow='console_identity_probe',
                   background_qualified=False,requires_fresh_target_identity=True,
                   request_id=pending['request_id'],lock_token_sha256=hashlib.sha256(pending['lock_token'].encode()).hexdigest())
    with store.lock:
        store.db.execute('BEGIN IMMEDIATE')
        try:
            check()
            if (store.get('binding','account')!=old or store.get('account_rebind','pending')!=pending or
                    store.get('console_identity',account['instance_id'])!=account or
                    any(store.get(bucket,'pending') for bucket in PENDING if bucket!='account_rebind')):
                raise ConsoleError('console_adoption_state_changed')
            archive=store.get('binding_epochs',old_key)
            if archive is not None and archive!=old:raise ConsoleError('console_adoption_archive_conflict')
            rows=[('binding','account',binding),('account_rebind',pending['request_id'],completed),
                  ('account_rebind','pending',None)]
            if archive is None:rows.insert(0,('binding_epochs',old_key,old))
            for bucket,key,value in rows:
                store.db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',(bucket,digest(key),store._pack(value)))
            check()
            store.db.commit()
        except BaseException:
            store.db.rollback()
            raise
    return completed


def run(args):
    from scripts.rebind_sender_profile import PENDING,original
    from scripts.development_window_restore import load_local_hashed
    from scripts.probe_console_identity import sources
    from scripts.recover_exited_navigation import WindowsProcessObserver,require_gone
    from wechat_agent_bridge.console_store import ConsoleStore
    from wechat_agent_bridge.protection import DPAPIProtector
    from wechat_agent_bridge.adapters.accessibility_gate import NativeGate
    directory=original.DIRECTORY
    receipt,receipt_sha=load_local_hashed(args.receipt)
    manifest=sources()
    report=dict(kind='console_identity_adoption',state='failed',identity_receipt_sha256=receipt_sha,
                cleanup_errors=[],history_read=False,draft_read=False,send_invoked=False,api_called=False,
                background_qualified=False)
    lock=original.RunLock(directory/'autofocus-draft.lock')
    store=native=None
    pending=None
    try:
        lock.acquire()
        store=ConsoleStore(directory/'gate.sqlite3',DPAPIProtector())
        if any(store.get(bucket,'pending') for bucket in PENDING):
            raise ConsoleError('console_adoption_prior_recovery_required')
        instance=receipt.get('identity_probe',{}).get('instance_id','')
        account=store.get('console_identity',instance)
        fp=validate(receipt,account,args.account,manifest)
        report['fingerprint']=asdict(fp)
        old=store.get('binding','account',{})
        require_old_epoch(old,account,fp)
        report['old_instance_exit_reason']=require_gone(old.get('fingerprint',{}),
            WindowsProcessObserver().inspect(old.get('fingerprint',{}).get('pid',0)))
        native=NativeGate(fp.hwnd)
        def check():
            if (directory/'STOP').exists():raise ConsoleError('console_adoption_emergency_stop')
            if native.fingerprint()!=fp or native.read()!=0:
                raise ConsoleError('console_adoption_current_identity_changed')
            if sources()!=manifest or load_local_hashed(args.receipt)[1]!=receipt_sha:
                raise ConsoleError('console_adoption_sources_changed')
        check()
        pending=dict(request_id='console-adopt-'+uuid.uuid4().hex,fingerprint=asdict(fp),
                     identity_receipt_sha256=receipt_sha,lock_token=lock.token)
        store.put('account_rebind','pending',pending)
        report.update(commit(store,old,account,fp,receipt_sha,pending,check))
    except Exception as exc:
        report.update(state='recovery_required' if pending else 'failed',
                      reason=str(exc) if isinstance(exc,ConsoleError) else 'console_identity_adoption_failed')
    finally:
        if native:
            try:native.close()
            except Exception:report['cleanup_errors'].append('console_adoption_handle_close_failed')
        if store:
            try:
                if report['state']=='sender_rebound' or pending is None:
                    if not report['cleanup_errors'] and not store.get('account_rebind','pending'):lock.release()
            finally:store.close()
        elif lock.owned:lock.release()
    if report['cleanup_errors']:report['state']='recovery_required'
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--adopt',action='store_true')
    parser.add_argument('--receipt')
    parser.add_argument('--account')
    args=parser.parse_args(argv)
    if not args.adopt:
        print(json.dumps(dict(state='disabled',account_binding_modified=False)))
        return 0
    report=run(args)
    from scripts.probe_console_identity import save_report
    from wechat_agent_bridge.acceptance import acceptance_directory
    result=save_report(acceptance_directory(),'console-identity-adoption',report)
    print(json.dumps(dict(**result,state=report['state'],reason=report.get('reason'))))
    return 0 if report['state']=='sender_rebound' else 1


if __name__=='__main__':raise SystemExit(main())
