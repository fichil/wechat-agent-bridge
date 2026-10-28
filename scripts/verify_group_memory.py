"""Opt-in live group reading and the application's real 180-second summary timer.

Never sends or navigates. A single verified group and one summary request only;
unknown sender semantics stay unknown. Default CLI and unit tests are offline.
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
from wechat_agent_bridge.acceptance import AcceptanceLedger,AcceptanceSession,MODEL
from wechat_agent_bridge.console_models import Account,Conversation,ChatMessage,MessagePage,ConsoleError
from wechat_agent_bridge.console_service import ConsoleService
from wechat_agent_bridge.console_store import ConsoleStore,digest
from wechat_agent_bridge.protection import DPAPIProtector

CASE='bound-group-memory-20261026-001'
INCREMENTAL_CASE='bound-group-memory-20261026-002'


class IncrementalFixture:
    """Exact prior summary plus one observed synthetic echo; never replay history."""
    def __init__(self,previous,sent,memory,rows,fp):
        from scripts.verify_bound_signal_send import MEMORY_TEXT
        self.text=MEMORY_TEXT
        self.previous_hash=hashlib.sha256(memory.get('summary','').encode()).hexdigest()
        self.watermark=memory.get('watermark')
        if (previous.get('state')!='timer_summary_saved_and_restart_paused' or
            previous.get('fingerprint')!=fp or previous.get('cleanup_errors')!=[] or
            previous.get('summary',{}).get('sha256')!=self.previous_hash or
            previous.get('summary',{}).get('watermark')!=self.watermark or
            type(self.watermark) is not int or self.watermark<=0 or
            not all(x in memory.get('summary','') for x in ('M1','002')) or
            not rows or max(r['seq'] for r in rows)!=self.watermark):
            raise ConsoleError('group_memory_previous_summary_not_verified')
        worker=sent.get('worker',{}); native=worker.get('native_evidence',{}); cleanup=native.get('cleanup',{})
        if (sent.get('kind')!='bound_group_memory_fixture_candidate' or sent.get('candidate_case')!='memory-003' or
            sent.get('fingerprint')!=fp or sent.get('state')!='message_echo_observed' or
            sent.get('desktop_unchanged') is not True or sent.get('events_observed')!=0 or
            sent.get('patch_restored') is not True or sent.get('worker_exited') is not True or
            sent.get('sources_unchanged') is not True or sent.get('cleanup_errors')!=[] or
            worker.get('draft_empty_after') is not True or worker.get('new_exact_echo_count')!=1 or
            native.get('method_returned') is not True or native.get('exception_code')!=0 or
            native.get('entries')!=1 or native.get('gui_thread_match') is not True or
            cleanup.get('helper_unloaded') is not True or cleanup.get('errors')!=[]):
            raise ConsoleError('group_memory_fixture_send_not_verified')
        rid=worker.get('echo_runtime_id')
        if (not isinstance(rid,list) or len(rid)!=4 or any(type(x) is not int for x in rid) or
            rid[:3]!=[42,fp['hwnd'],4] or not -2**31<=rid[3]<-1):
            raise ConsoleError('group_memory_fixture_identity_invalid')
        self.id=digest(str(rid),self.text)
        self.old_order=[r['id'] for r in rows]
        self.old_ids=set(self.old_order)
        if self.id in self.old_ids: raise ConsoleError('group_memory_fixture_already_saved')
        self.last_page_coverage=None

    def page(self,page):
        ids=[m.id for m in page.messages]
        # Loaded-tail visibility may discard only a leading prefix. Require the
        # exact ordered suffix, including the last old row, then this one echo.
        visible_old=len(ids)-1
        if (not 1<=visible_old<=len(self.old_order) or
            ids!=self.old_order[-visible_old:]+[self.id]):
            raise ConsoleError('group_memory_incremental_rows_changed')
        if sum(m.id==self.id and m.text==self.text for m in page.messages)!=1:
            raise ConsoleError('group_memory_fixture_text_changed')
        self.last_page_coverage=dict(known_rows_visible=visible_old,
            known_leading_rows_outside_loaded_tail=len(self.old_order)-visible_old,
            last_old_row_verified=True,new_fixture_rows=1)

    def context(self,summary,messages):
        if (hashlib.sha256(summary.encode()).hexdigest()!=self.previous_hash or len(messages)!=1 or
            messages[0].get('text')!=self.text or messages[0].get('seq')!=self.watermark+1):
            raise ConsoleError('group_memory_incremental_context_mismatch')

    @staticmethod
    def facts(summary):
        return dict(previous_marker_retained=all(x in summary for x in ('M1','002')),
            project_code_retained='BRIDGE-MEM-003' in summary,
            date_retained=any(x in summary for x in ('2026-10-27','2026年10月27日')),
            time_retained=any(x in summary for x in ('14:30','14：30')))


def text_capability(report):
    if (report.get('kind')!='synthetic_provider_acceptance' or report.get('model')!=MODEL or
        report.get('contains_real_chat') is not False or
        not any(r.get('capability')=='text' and r.get('attempted') is True and r.get('ok') is True
                for r in report.get('results',[]))):
        raise ConsoleError('group_memory_text_evidence_required')


def local_evidence(path):
    path=Path(path).resolve()
    if path.parent!=original.DIRECTORY.resolve() or path.stat().st_size>1000000:
        raise ConsoleError('group_memory_local_evidence_required')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def decode_capture(report,expected,protector,directory):
    worker=report.get('worker',{})
    if (report.get('state')!='preflight_verified' or report.get('attempted') is not False or
        report.get('patch_restored') is not True or report.get('worker_exited') is not True or
        report.get('desktop_unchanged') is not True or report.get('events_observed')!=0 or
        report.get('source_unchanged') is not True or report.get('cleanup_errors')!=[] or
        worker.get('setvalue_called') is not False or worker.get('invoke_called') is not False):
        raise ConsoleError('group_memory_read_not_verified')
    original.check_draft_target(expected,report.get('observed_target_metadata',{}))
    path=Path(worker.get('protected_capture','')).resolve()
    if path.parent!=Path(directory).resolve() or path.suffix!='.dpapi' or path.stat().st_size>1000000:
        raise ConsoleError('group_memory_capture_path_invalid')
    encrypted=path.read_bytes()
    if hashlib.sha256(encrypted).hexdigest()!=worker.get('protected_sha256'):
        raise ConsoleError('group_memory_capture_hash_changed')
    value=json.loads(protector.unprotect(encrypted))
    if value.get('historical') is not True or value.get('coverage')!='accessible_loaded_tail_only':
        raise ConsoleError('group_memory_coverage_unknown')
    return value


from wechat_agent_bridge.adapters.bound_read import message_page, GroupReadAdapter


class OneSummaryProvider:
    simulation_only=False
    def __init__(self,provider,guard,adapter,started,clock=time.monotonic,context_check=None):
        self.provider,self.guard,self.adapter=provider,guard,adapter
        self.started,self.clock,self.calls=started,clock,[]
        self.context_check=context_check
    def summarize(self,summary,messages):
        self.guard()
        now=self.clock()
        if self.calls: raise ConsoleError('group_memory_one_summary_only')
        if now-self.started<180: raise ConsoleError('group_memory_real_timer_not_elapsed')
        if self.adapter.last_capture is None or now-self.adapter.last_capture>10:
            raise ConsoleError('group_memory_fresh_read_required')
        if self.context_check: self.context_check(summary,messages)
        self.calls.append(dict(elapsed=round(now-self.started,3),rows=len(messages),returned=False,
            previous_summary_sha256=hashlib.sha256(summary.encode()).hexdigest(),
            context_sha256=hashlib.sha256(json.dumps(messages,sort_keys=True).encode()).hexdigest(),
            incremental_context_verified=self.context_check is not None))
        value=self.provider.summarize(summary,messages)
        self.guard()
        self.calls[-1]['returned']=True
        return value
    def reply(self,*args): raise ConsoleError('acceptance_read_only_scope')


class NoClientAdapter:
    simulation_only=False
    def close(self): pass
    def __getattr__(self,name): raise ConsoleError('group_memory_restart_must_stay_paused')


def restart_worker(pipe,database,account,chat):
    store=ledger=service=None
    try:
        store=ConsoleStore(database,DPAPIProtector())
        ledger=AcceptanceLedger(original.DIRECTORY/'quota.sqlite3')
        before=ledger.status()
        scope=AcceptanceSession(ledger)
        service=ConsoleService(NoClientAdapter(),store,acceptance=scope)
        service.tick()  # Must return paused without client access or an API call.
        try: service.resume()
        except ConsoleError as exc: rejected=str(exc)=='acceptance_target_not_bound'
        else: rejected=False
        memory=store.get('memory',digest(account,chat),{})
        pipe.send(dict(ok=True,paused=service.paused,grants_paused=all(g.paused for g in service.grants.values()),
            resume_without_rebind_rejected=rejected,quota_unchanged=before==ledger.status(),
            summary_sha256=hashlib.sha256(memory.get('summary','').encode()).hexdigest(),
            watermark=memory.get('watermark'),scope_bound=scope.account is not None))
    except Exception:
        pipe.send(dict(ok=False,reason='group_memory_restart_check_failed'))
    finally:
        if service: service.close()
        if ledger: ledger.close()
        if store: store.close()
        pipe.close()


def check_restart(database,account,chat):
    ctx=mp.get_context('spawn'); parent,child=ctx.Pipe()
    process=ctx.Process(target=restart_worker,args=(child,str(database),account,chat))
    try:
        process.start(); child.close()
        if not parent.poll(20): raise ConsoleError('group_memory_restart_timeout')
        result=parent.recv(); process.join(2)
        if process.is_alive() or process.exitcode!=0: raise ConsoleError('group_memory_restart_exit_unconfirmed')
        return result
    finally:
        if process.pid:
            if process.is_alive(): process.terminate(); process.join(2)
            if not process.is_alive(): process.close()
        parent.close(); child.close()


def run(args):
    if not (args.upload_group_to_openai and args.allow_accessibility_patch and args.account and args.group and
            args.private and args.env_file and args.provider_evidence and args.target_evidence and args.synthetic_evidence):
        raise ConsoleError('group_memory_explicit_scope_and_upload_required')
    incremental=getattr(args,'incremental_fixture',False)
    if incremental and not (args.previous_memory_evidence and args.fixture_send_evidence):
        raise ConsoleError('group_memory_incremental_evidence_required')
    case=INCREMENTAL_CASE if incremental else CASE
    from scripts.probe_bound_uia_context import require_context_canary
    from scripts.probe_bound_message_labels import worker as capture_worker
    from wechat_agent_bridge.adapters.accessibility_gate import NativeGate
    from wechat_agent_bridge.adapters.background import snapshot,unchanged
    from wechat_agent_bridge.openai_config import load_openai_configuration
    from wechat_agent_bridge.providers import CompatibleProvider
    require_context_canary(args.synthetic_evidence)
    text_capability(local_evidence(args.provider_evidence))
    protector=DPAPIProtector(); directory=original.DIRECTORY
    state=ConsoleStore(directory/'gate.sqlite3',protector)
    native=store=ledger=service=None
    lock=original.RunLock(directory/'group-memory.lock')
    report=dict(kind='bound_group_timer_acceptance',state='failed',send_invoked=False,
        case=case,m1_complete=False,summary_semantics_verified=False,captures=[],cleanup_errors=[])
    claimed=False
    try:
        lock.acquire()
        if state.get('memory_trials',case): raise ConsoleError('group_memory_case_already_attempted')
        if any(state.get(k,'pending') for k in ('patch','thread_hook','draft_probe','development_navigation','development_window_restore','development_exact_search')):
            raise ConsoleError('group_memory_prior_recovery_required')
        binding=state.get('binding','account',{})
        expected=original.validate_binding(binding,local_evidence(args.target_evidence),args.account,binding.get('fingerprint'))
        native=NativeGate(binding['fingerprint']['hwnd']); fp=native.fingerprint()
        if asdict(fp)!=binding['fingerprint']: raise ConsoleError('group_memory_process_changed')
        baseline=snapshot(fp.pid); unchanged(baseline,baseline)
        def guard():
            if (directory/'STOP').exists(): raise ConsoleError('group_memory_emergency_stop')
            if native.fingerprint()!=fp or state.get('binding','account')!=binding:
                raise ConsoleError('group_memory_binding_changed')
            unchanged(baseline,snapshot(fp.pid))
        args.run,args.preflight,args.operator_idle=False,True,False
        def capture():
            guard()
            result=original.run(args,inspection_worker=capture_worker)
            path=directory/('group-memory-read-'+uuid.uuid4().hex+'.json')
            with path.open('x',encoding='utf-8') as stream: json.dump(result,stream,indent=2)
            report['captures'].append(dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            value=decode_capture(result,expected,protector,directory)
            guard(); return value
        account=Account(binding['account']['id'],args.account,args.account,
            instance_id=str(fp.pid)+':'+str(fp.hwnd),version=fp.version,
            identity_source=binding['account']['identity_source'],fingerprint=asdict(fp))
        chat=Conversation(account.id,'bound-group-'+digest(str(fp.created),str(expected['session_runtime_id'])),
            args.group,'group',identity_source='exact_selected_row_same_process_lifetime')
        adapter=GroupReadAdapter(account,chat,fp.hwnd,capture,guard)
        first=adapter.messages(account.id,chat.id)
        report.update(fingerprint=asdict(fp),baseline_text_rows=len(first.messages),coverage=first.limitation)
        ledger=AcceptanceLedger(directory/'quota.sqlite3'); report['quota_before']=ledger.status()
        scope=AcceptanceSession(ledger); scope.bind_group_reading(account,chat)
        config=load_openai_configuration(args.env_file); scope.validate_provider(config)
        config['capabilities']={'text':True,'vision':False,'transcription':False}
        database=directory/'group-memory.sqlite3'
        store=ConsoleStore(database,protector); store.put('settings','provider',config)
        fixture=None
        if incremental:
            previous=local_evidence(args.previous_memory_evidence)
            recorded=state.get('memory_trials',CASE,{})
            if previous.get('summary')!=recorded.get('summary') or recorded.get('state')!='timer_summary_saved_and_restart_paused':
                raise ConsoleError('group_memory_previous_journal_mismatch')
            fixture=IncrementalFixture(previous,local_evidence(args.fixture_send_evidence),
                store.get('memory',digest(account.id,chat.id),{}),
                store.messages(account.id,chat.id,limit=50),asdict(fp))
            fixture.page(first); adapter.validate_page=fixture.page
            report['incremental_loaded_tail']=fixture.last_page_coverage
            report['previous_summary_sha256']=fixture.previous_hash
            report['previous_watermark']=fixture.watermark
            report['evidence_sha256']={name:hashlib.sha256(Path(value).read_bytes()).hexdigest()
                for name,value in (('previous_memory',args.previous_memory_evidence),('fixture_send',args.fixture_send_evidence))}
        started=time.monotonic()
        provider=OneSummaryProvider(CompatibleProvider(config),guard,adapter,started,
            context_check=fixture.context if fixture else None)
        service=ConsoleService(adapter,store,acceptance=scope,provider_factory=lambda c:provider)
        service.discover()
        service.authorize(account.id,dict(read=True,upload=True,reply=False,paused=False,excluded=[]))
        before=service.memory(account.id,chat.id)
        state.put('memory_trials',case,dict(state='running',fingerprint=asdict(fp),database=str(database)))
        claimed=True
        service.resume()
        print(json.dumps(dict(phase='waiting_for_real_timer',minimum_seconds=180,send_enabled=False)),flush=True)
        while time.monotonic()-started<220:
            guard(); service.tick()
            if service.errors: raise ConsoleError(service.errors[-1]['reason'])
            memory=service.memory(account.id,chat.id)
            if memory['watermark']>before['watermark'] and memory['summary']:
                break
            if service.paused: raise ConsoleError('group_memory_paused_before_summary')
            time.sleep(.5)
        else: raise ConsoleError('group_memory_timer_deadline')
        service.pause()
        report['summary']=dict(source_start=memory['source_start'],watermark=memory['watermark'],
            sha256=hashlib.sha256(memory['summary'].encode()).hexdigest(),characters=len(memory['summary']))
        report['calls']=provider.calls
        if len(provider.calls)!=1 or provider.calls[0]['returned'] is not True:
            raise ConsoleError('group_memory_summary_call_unconfirmed')
        if fixture:
            report['incremental_loaded_tail_final']=fixture.last_page_coverage
            report['fixture_checks']=fixture.facts(memory['summary'])
            report['fixture_facts_verified']=all(report['fixture_checks'].values())
            if not report['fixture_facts_verified']: raise ConsoleError('group_memory_fixture_facts_missing')
        report['restart']=check_restart(database,account.id,chat.id)
        restart=report['restart']
        if (not all(restart.get(k) is True for k in ('ok','paused','grants_paused','resume_without_rebind_rejected','quota_unchanged'))
            or restart.get('scope_bound') is not False or restart.get('summary_sha256')!=report['summary']['sha256']
            or restart.get('watermark')!=memory['watermark']):
            raise ConsoleError('group_memory_restart_not_verified')
        guard(); report['state']='timer_summary_saved_and_restart_paused'
    except KeyboardInterrupt:
        report['reason']='group_memory_operator_cancelled'
    except Exception as exc:
        report['reason']=str(exc) if isinstance(exc,ConsoleError) else 'group_memory_check_failed'
    finally:
        if service:
            try: service.close()
            except Exception: report['cleanup_errors'].append('group_memory_service_close_failed')
        if ledger:
            try: report['quota_after']=ledger.status()
            except Exception: report['cleanup_errors'].append('group_memory_quota_status_failed')
        for label,resource in (('ledger',ledger),('store',store),('native',native)):
            if resource is not None:
                try: resource.close()
                except Exception: report['cleanup_errors'].append('group_memory_'+label+'_close_failed')
        if report['cleanup_errors']: report['state']='recovery_required'
        try:
            if claimed: state.put('memory_trials',case,report)
            lock.release()
        finally: state.close()
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--run-bound-memory',action='store_true')
    parser.add_argument('--upload-group-to-openai',action='store_true')
    parser.add_argument('--allow-accessibility-patch',action='store_true')
    parser.add_argument('--incremental-fixture',action='store_true')
    for flag in ('account','group','private','env-file','provider-evidence','target-evidence','synthetic-evidence',
                 'previous-memory-evidence','fixture-send-evidence'):
        parser.add_argument('--'+flag)
    args=parser.parse_args(argv)
    if not args.run_bound_memory:
        print(json.dumps(dict(state='disabled',api_called=False,send_invoked=False))); return 0
    try: report=run(args)
    except Exception as exc:
        report=dict(state='failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'group_memory_preparation_failed')
    report['source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    path=original.DIRECTORY/('group-memory-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as stream: json.dump(report,stream,indent=2)
    print(json.dumps(dict(path=str(path),state=report['state'],reason=report.get('reason'))))
    return 0 if report['state']=='timer_summary_saved_and_restart_paused' else 1


if __name__=='__main__': raise SystemExit(main())
