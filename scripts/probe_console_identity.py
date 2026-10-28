"""Explicit read-only integration of the real console identity-wait flow.

Starts a temporary authenticated loopback console, with no chat scope/grants or
provider tests. Uses the original fixed databases and quotas. Default disabled.
The application (not a direct UI input script) owns its bounded enrollment task.
"""
import argparse
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from wechat_agent_bridge.console_models import ConsoleError


def original_records(path):
    """Only hashes of existing ciphertext; never decrypt history or unknowns."""
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as connection:
        return {(bucket,key):hashlib.sha256(payload).hexdigest()
                for bucket,key,payload in connection.execute('SELECT bucket,key,payload FROM records')
                if bucket not in ('patch','console_identity','console_runtime')}


def sources():
    paths=('scripts/probe_console_identity.py','wechat_agent_bridge/identity_probe.py',
           'wechat_agent_bridge/console_service.py','wechat_agent_bridge/console_http.py',
           'wechat_agent_bridge/adapters/windows.py','wechat_agent_bridge/adapters/windows_uia.py',
           'wechat_agent_bridge/adapters/background.py','wechat_agent_bridge/adapters/accessibility_gate.py')
    return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}


def save_report(directory,kind,report):
    path=directory/(kind+'-'+uuid.uuid4().hex+'.json')
    raw=(json.dumps(report,indent=2)+'\n').encode()
    with path.open('xb') as stream:stream.write(raw)
    return dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest())


def worker_exit_evidence(process):
    if process is None:return dict(worker_started=False,worker_exited=True)
    if process.is_alive() or process.exitcode is None:
        raise ConsoleError('console_identity_worker_exit_unconfirmed')
    return dict(worker_started=True,worker_exited=True,worker_exit_code=process.exitcode)


def run(args):
    from scripts.rebind_sender_profile import require_receipts,PENDING,original
    from scripts.development_window_restore import load_local_hashed
    from wechat_agent_bridge.acceptance import acceptance_directory,AcceptanceLedger,AcceptanceSession
    from wechat_agent_bridge.console_models import Account
    from wechat_agent_bridge.console_store import ConsoleStore,digest
    from wechat_agent_bridge.protection import DPAPIProtector
    from wechat_agent_bridge.adapters.accessibility_gate import NativeGate
    from wechat_agent_bridge.adapters.windows import WindowsAdapter
    from wechat_agent_bridge.console_service import ConsoleService
    from wechat_agent_bridge.console_http import ConsoleServer
    directory=acceptance_directory()
    report=dict(kind='console_identity_integration',state='failed',cleanup_errors=[],
        sources=sources(),history_read=False,draft_read=False,send_invoked=False,api_called=False,
        acceptance_binding_modified=False,background_qualified=False)
    lock=original.RunLock(directory/'autofocus-draft.lock')
    gate_store=store=ledger=service=adapter=server=server_thread=native=None
    before=None
    quota_before=None
    runtime_key=None
    try:
        if not args.allow_accessibility_patch:
            raise ConsoleError('console_identity_explicit_patch_opt_in_required')
        evidence,evidence_sha=load_local_hashed(args.fingerprint_evidence)
        cua,cua_sha=load_local_hashed(args.cua_evidence)
        fp=require_receipts(evidence,evidence_sha,cua,args.account)
        report.update(fingerprint=asdict(fp),fingerprint_evidence_sha256=evidence_sha,
                      cua_evidence_sha256=cua_sha,account_sha256=hashlib.sha256(args.account.encode()).hexdigest())
        if any(not (directory/name).is_file() for name in ('gate.sqlite3','quota.sqlite3')):
            raise ConsoleError('console_identity_original_databases_required')
        lock.acquire()
        protector=DPAPIProtector()
        gate_store=ConsoleStore(directory/'gate.sqlite3',protector)
        if any(gate_store.get(bucket,'pending') for bucket in PENDING):
            raise ConsoleError('console_identity_prior_recovery_required')
        if (directory/'STOP').exists():raise ConsoleError('console_identity_emergency_stop')
        before=original_records(directory/'gate.sqlite3')
        quota_before=hashlib.sha256((directory/'quota.sqlite3').read_bytes()).hexdigest()
        native=NativeGate(fp.hwnd)
        if native.fingerprint()!=fp or native.read()!=0:
            raise ConsoleError('console_identity_current_fingerprint_or_byte_changed')
        native.close();native=None
        # Exactly the normal console components and original files. No provider
        # configuration import, model test, scope binding, resume or chat grant.
        store=ConsoleStore(directory/'windows-state.sqlite3',protector)
        ledger=AcceptanceLedger(directory/'quota.sqlite3')
        report['usage_before']={k:v for k,v in ledger.status().items()
                                if k in ('api_used','api_limit','send_used','send_limit')}
        adapter=WindowsAdapter(allow_accessibility_patch=True,gate_store=gate_store)
        service=ConsoleService(adapter,store,acceptance=AcceptanceSession(ledger))
        instance=f'{fp.pid}:{fp.hwnd}'
        candidate=Account('candidate-'+digest(instance),'待核验微信窗口',instance_id=instance,
                          version=fp.version,identity_certain=False)
        # Pending instance comes from the verified native receipt, not from a
        # nickname or invented account identity. The ordinary API rechecks it.
        service.accounts_cache={candidate.id:candidate}
        service.accessibility('bind',dict(instance_id=instance,wechat_id=args.account))
        interactive=getattr(args,'serve_console',False)
        if not interactive:service.accessibility('enable',{})
        server=ConsoleServer(service)
        server_thread=threading.Thread(target=server.serve_forever,daemon=True,name='identity-console-http')
        server_thread.start()
        service.start()
        result=(dict(state='awaiting_console_action') if interactive else
                service.identity_probe('start',dict(instance_id=instance)))
        ready=dict(kind='console_identity_runtime',state=result['state'],pid=os.getpid(),origin=server.origin,
                   fingerprint=asdict(fp),paused=service.paused,chat_scope_bound=False,
                   automatic_chat_grants=False,bootstrap_token_disclosed=False,max_wait_seconds=60,
                   max_start_seconds=300 if interactive else 0,
                   patch_enabled=adapter.gate.state in ('enabled','already_enabled'))
        if interactive:
            # Normal browser bootstrap, stored only under the console's DPAPI
            # protection; never print its token or put it in the ready receipt.
            import webbrowser
            runtime_key=uuid.uuid4().hex
            store.put('console_runtime',runtime_key,dict(launch_url=server.launch_url))
            ready['runtime_key']=runtime_key
            ready['browser_opened']=bool(webbrowser.open(server.launch_url))
        receipt=save_report(directory,'console-identity-runtime',ready)
        print(json.dumps(dict(**receipt,state=result['state'],pid=os.getpid())),flush=True)
        if interactive:
            # No desktop poll or accessibility patch here. The browser starts
            # the actual 60s task after setup is visible, not on thread wakeup.
            deadline=time.monotonic()+300
            while True:
                with service.lock:task=service.identity_probe_task
                if task is not None:break
                if time.monotonic()>=deadline:raise ConsoleError('console_identity_start_timeout')
                if (directory/'STOP').exists():raise ConsoleError('console_identity_emergency_stop')
                if sources()!=report['sources']:raise ConsoleError('console_identity_sources_changed')
                time.sleep(.1)
        else:
            task=service.identity_probe_task
        # Observe this same process/task, never start another because a tool
        # observation timed out. The application itself enforces the deadline.
        task.thread.join()
        report['identity_probe']=task.status()
        report['state']='verified' if report['identity_probe']['state']=='verified' else 'failed'
        report['console_paused']=service.paused
        report['chat_scope_bound']=service.acceptance.account is not None
        if adapter.native_gate:
            report['fingerprint_unchanged']=adapter.native_gate.fingerprint()==fp
            report['restored_byte']=adapter.native_gate.read()
        report['usage_after']={k:v for k,v in ledger.status().items()
                               if k in ('api_used','api_limit','send_used','send_limit')}
        if report['state']=='verified' and not (
                report['console_paused'] is True and report['chat_scope_bound'] is False and
                report.get('fingerprint_unchanged') is True and report.get('restored_byte')==0 and
                report['identity_probe']['restored'] is True and report['identity_probe']['identity_saved'] is True):
            report.update(state='failed',reason='console_identity_completion_not_proven')
    except Exception as exc:
        report.update(state='failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'console_identity_integration_failed')
    finally:
        ui_worker=adapter.process if adapter else None
        if server:
            try:
                if server_thread and server_thread.is_alive():server.shutdown()
                server.server_close()
                if server_thread:server_thread.join(2)
                if server_thread and server_thread.is_alive():raise RuntimeError()
            except Exception:report['cleanup_errors'].append('console_server_cleanup_failed')
        try:
            if service:service.close()
            elif adapter:adapter.close()
        except Exception:report['cleanup_errors'].append('console_service_cleanup_failed')
        try:report.update(worker_exit_evidence(ui_worker))
        except Exception:report['cleanup_errors'].append('console_identity_worker_exit_unconfirmed')
        if native:
            try:native.close()
            except Exception:report['cleanup_errors'].append('console_preflight_handle_cleanup_failed')
        if before is not None:
            try:
                after=original_records(directory/'gate.sqlite3')
                report['original_ciphertexts_unchanged']=all(after.get(key)==value for key,value in before.items())
            except Exception:report['cleanup_errors'].append('console_original_ciphertext_audit_failed')
        if quota_before is not None:
            try:
                report['quota_file_unchanged']=hashlib.sha256((directory/'quota.sqlite3').read_bytes()).hexdigest()==quota_before
            except Exception:report['cleanup_errors'].append('console_quota_audit_failed')
        if gate_store:
            try:report['patch_pending_clear']=not bool(gate_store.get('patch','pending'))
            except Exception:report['cleanup_errors'].append('console_patch_audit_failed')
        report['sources_unchanged']=sources()==report['sources']
        if (report['cleanup_errors'] or report.get('original_ciphertexts_unchanged') is not True or
                report.get('quota_file_unchanged') is not True or report.get('patch_pending_clear') is not True or
                report['sources_unchanged'] is not True):
            report['state']='failed'
        if lock.owned and not report['cleanup_errors'] and report.get('patch_pending_clear') is True:
            try:lock.release()
            except Exception:report['cleanup_errors'].append('console_run_lock_release_failed')
        if runtime_key and store:
            try:store.put('console_runtime',runtime_key,None)
            except Exception:report['cleanup_errors'].append('console_runtime_bootstrap_cleanup_failed')
        for item in (store,ledger,gate_store):
            if item:
                try:item.close()
                except Exception:report['cleanup_errors'].append('console_store_close_failed')
    if report['cleanup_errors']:report['state']='failed'
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--allow-accessibility-patch',action='store_true')
    parser.add_argument('--serve-console',action='store_true',help='Open paused console first; start the 60s probe from its UI')
    for field in ('account','fingerprint-evidence','cua-evidence'):parser.add_argument('--'+field)
    args=parser.parse_args(argv)
    if not args.run:
        print(json.dumps(dict(state='disabled',send_invoked=False,api_called=False)))
        return 0
    report=run(args)
    from wechat_agent_bridge.acceptance import acceptance_directory
    result=save_report(acceptance_directory(),'console-identity-result',report)
    print(json.dumps(dict(**result,state=report['state'],reason=report.get('reason'))),flush=True)
    return 0 if report['state']=='verified' else 1


if __name__=='__main__':raise SystemExit(main())
