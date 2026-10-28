"""One separately budgeted, fixed-message Qt clicked candidate experiment.

Old UIA Press remains disabled. Requires same-build native recovery and a
successful mode-zero load/unload in this exact account/process and test group.
"""
import argparse
from functools import partial
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import verify_bound_send as original_send
from scripts import probe_bound_thread_hook as hook
from scripts.test_thread_hook import validate_build
from wechat_agent_bridge.console_models import ConsoleError

TEST_TEXT='微信 Agent Bridge M1 验证（后台适配 002）'
GRANT='bound-clicked-20261026-002'
MEMORY_TEXT='微信 Agent Bridge M1 记忆验证 003：项目编号 BRIDGE-MEM-003；测试会议时间为 2026-10-27 14:30；请仅作为测试记录。'
MEMORY_GRANT='bound-memory-20261026-003'
PRIVATE_TEXT='微信 Agent Bridge M1 私聊验证 004'
PRIVATE_GRANT='bound-private-20261026-004'
CASES={
    'send-002':(TEST_TEXT,GRANT,
        'Independent reviewed clicked-signal candidate; original fixed unknown reservation retained; distinct numbered test text.'),
    'memory-003':(MEMORY_TEXT,MEMORY_GRANT,
        'One fixed synthetic group memory fixture 003; original fixed unknown and numbered 002 reservations retained.'),
    'private-004':(PRIVATE_TEXT,PRIVATE_GRANT,
        'One fixed private-chat background send after exact contact ID and immutable history baseline; all previous reservations retained.')}


def require_readonly(path,fp,build,*,desktop_scope='separated_desktops',target_kind='group',recipient_id_sha256=None):
    path=Path(path).resolve()
    if path.parent!=hook.original.DIRECTORY.resolve() or path.stat().st_size>200000:
        raise ConsoleError('signal_local_readonly_evidence_required')
    report=json.loads(path.read_text(encoding='utf-8-sig'))
    w=report.get('worker',{}); i=w.get('inspection',{}); cleanup=i.get('cleanup',{})
    if (desktop_scope not in ('separated_desktops','current_desktop_background') or
        target_kind not in ('group','private') or report.get('target_kind','group')!=target_kind or
        (target_kind=='private' and (not recipient_id_sha256 or report.get('recipient_id_sha256')!=recipient_id_sha256 or
            report.get('worker_exited') is not True or report.get('source_unchanged') is not True)) or
        report.get('desktop_scope','separated_desktops')!=desktop_scope or
        report.get('state')!='preflight_verified' or report.get('build')!=build or w.get('fingerprint')!=fp or
        report.get('patch_restored') is not True or report.get('desktop_unchanged') is not True or
        report.get('events_observed')!=0 or report.get('cleanup_errors')!=[] or
        i.get('qt_method_called') is not False or i.get('send_invoked') is not False or
        i.get('metadata_checked') is not True or i.get('selected') is not True or i.get('gui_thread_match') is not True or
        cleanup.get('owner_exitcode')!=0 or cleanup.get('unhooked')!=1 or cleanup.get('drained')!=1 or
        any(cleanup.get(k) is not True for k in ('post_unhook_delivered','post_unhook_no_callback','helper_unloaded','mapping_handles_closed')) or
        cleanup.get('errors')!=[]):
        raise ConsoleError('signal_exact_process_readonly_proof_required')
    return w['runtimes']


class HookPort(original_send.UIAPort):
    def __init__(self,fp,names,expected,*,hook_evidence,readonly_evidence,case='send-002',desktop_scope='separated_desktops',
                 target_kind='group',recipient_id_sha256=None):
        if case not in CASES: raise ConsoleError('signal_fixed_case_required')
        super().__init__(fp,names,expected)
        self.hook_evidence,self.readonly_evidence=hook_evidence,readonly_evidence
        self.text=CASES[case][0]; self.desktop_scope=desktop_scope
        self.target_kind,self.recipient_id_sha256=target_kind,recipient_id_sha256
        self.native_evidence={}

    def require_send_capability(self):
        build=hook.require_recovery(self.hook_evidence,bound_send=True)
        runtimes=require_readonly(self.readonly_evidence,self.fp,build,desktop_scope=self.desktop_scope,
            target_kind=getattr(self,'target_kind','group'),recipient_id_sha256=getattr(self,'recipient_id_sha256',None))
        current=[self.expected['input_runtime_id'],list(self.send_bundle[3]),self.expected['session_runtime_id']]
        if runtimes!=current: raise ConsoleError('signal_readonly_target_changed')

    def invoke(self):
        self.require_send_capability()
        from wechat_agent_bridge.adapters.accessibility_gate import NativeGate,Fingerprint
        from wechat_agent_bridge.adapters.runtime_accessibility import ExactProfile,ReadOnlyProcess,BoundedMemory,interface_for_id
        from scripts.probe_send_accessible import inspect_interface,inspect_session_guard
        sys.path.insert(0,str(ROOT/'.local/static-tools'))
        fp=Fingerprint(**self.fp); native=reader=probe=None
        try:
            native=NativeGate(fp.hwnd)
            if native.fingerprint()!=fp: raise ConsoleError('signal_process_changed')
            profile=ExactProfile(native.path.read_bytes()); reader=ReadOnlyProcess(fp,native.fingerprint)
            memory=BoundedMemory(reader.read,reader.check,max_bytes=32768)
            inspect_interface(memory,fp,profile,list(self.send_bundle[3]),inspect_connections=True)
            inspect_session_guard(memory,fp,profile,self.expected['session_runtime_id'])
            runtimes=[self.expected['input_runtime_id'],list(self.send_bundle[3]),self.expected['session_runtime_id']]
            widgets=[]
            for runtime in runtimes:
                iface=interface_for_id(memory,fp.module_base,runtime)
                weak=memory.number(iface+8); control=memory.number(weak)
                if not control or memory.number(control+4,'<i')==0: raise ConsoleError('signal_widget_destroyed')
                widgets.append(memory.number(weak+8))
            memory.recheck()
            def check():
                self.checkpoint('bound_signal_checkpoint'); self.verify_target()
            def before_dispatch():
                check(); self.guard_patterns()
                if self.draft()!=self.text: raise ConsoleError('signal_draft_changed_before_dispatch')
                memory.recheck()
                if native.fingerprint()!=fp: raise ConsoleError('signal_process_changed')
            before_dispatch()
            probe=hook.BoundProbe(fp,native,check,send_candidate=True)
            self.native_evidence=probe.report
            probe.run(runtimes,widgets,emit=True,before_dispatch=before_dispatch)
        finally:
            try:
                if probe: probe.close()
            finally:
                if reader: reader.close()
                if native: native.close()


class Candidate:
    name='bound_clicked_signal_candidate'
    slot='extra:'+GRANT

    def __init__(self,hook_evidence,readonly_evidence,*,case='send-002',desktop_scope='separated_desktops',target_scope=None):
        if case not in CASES: raise ConsoleError('signal_fixed_case_required')
        private=case=='private-004'
        if private!=(target_scope is not None) or (private and target_scope.kind!='private'):
            raise ConsoleError('signal_case_target_scope_mismatch')
        self.target_scope=target_scope
        self.target_kind='private' if private else 'group'
        self.recipient_id_sha256=target_scope.metadata()['recipient_id_sha256'] if private else None
        self.case,self.desktop_scope=case,desktop_scope
        self.text,self.grant_id,self.reason=CASES[case]
        self.slot='extra:'+self.grant_id
        if case=='memory-003': self.name='bound_group_memory_fixture_candidate'
        if private: self.name='bound_private_fixed_candidate'
        self.hook_evidence,self.readonly_evidence=hook_evidence,readonly_evidence
        self.worker=partial(original_send.worker,port_factory=partial(HookPort,
            hook_evidence=hook_evidence,readonly_evidence=readonly_evidence,case=case,
            desktop_scope=desktop_scope,target_kind=self.target_kind,recipient_id_sha256=self.recipient_id_sha256),text=self.text)

    def require_capability(self,fp):
        build=hook.require_recovery(self.hook_evidence,bound_send=True)
        require_readonly(self.readonly_evidence,fp,build,desktop_scope=self.desktop_scope,
            target_kind=self.target_kind,recipient_id_sha256=self.recipient_id_sha256)

    def grant(self,ledger):
        return ledger.extend('send',self.grant_id,1,
            authorization='operator-20261026-quota-expansion-and-process-helper-approved',
            reason=self.reason)

    @staticmethod
    def sources():
        paths=('scripts/verify_bound_signal_send.py','scripts/probe_bound_thread_hook.py',
            'scripts/private_test_scope.py','scripts/contact_profile_identity.py','scripts/verify_contact_profile.py',
            'scripts/probe_send_accessible.py','scripts/test_thread_hook.py','scripts/build_thread_hook.py',
            'wechat_agent_bridge/adapters/runtime_accessibility.py')
        result={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}
        build=validate_build(bound_inspector=True,bound_send=True)
        result['bound_send_build']=hashlib.sha256(json.dumps(build,sort_keys=True).encode()).hexdigest()
        return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    mode=parser.add_mutually_exclusive_group()
    for flag in ('preflight','run-fixed-send','inspect-after-attempt','clear-test-draft'):
        mode.add_argument('--'+flag,action='store_true')
    parser.add_argument('--allow-process-helper',action='store_true')
    parser.add_argument('--allow-accessibility-patch',action='store_true')
    parser.add_argument('--case',choices=tuple(CASES),default='send-002')
    parser.add_argument('--current-desktop-background',action='store_true')
    parser.add_argument('--target-kind',choices=('group','private'),default='group')
    parser.add_argument('--private-label')
    for flag in ('account','group','private','target-evidence','synthetic-evidence','virtual-desktop','hook-evidence','readonly-evidence'):
        parser.add_argument('--'+flag)
    args=parser.parse_args(argv)
    if not any((args.preflight,args.run_fixed_send,args.inspect_after_attempt,args.clear_test_draft)):
        print(json.dumps(dict(state='disabled',send_enabled=False))); return 0
    try:
        if not args.allow_process_helper or not args.hook_evidence or not args.readonly_evidence:
            raise ConsoleError('signal_explicit_helper_and_evidence_required')
        scope=None
        if args.target_kind=='private':
            from scripts.private_test_scope import PrivateTestScope
            scope=PrivateTestScope(args.private,args.private_label)
        if (args.case=='private-004')!=(scope is not None):
            raise ConsoleError('signal_case_target_scope_mismatch')
        candidate=Candidate(args.hook_evidence,args.readonly_evidence,case=args.case,
            desktop_scope='current_desktop_background' if args.current_desktop_background else 'separated_desktops',target_scope=scope)
        report=original_send.run(args,candidate=candidate)
    except Exception as exc:
        report=dict(state='failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'signal_preparation_failed',send_attempted=False)
    path=hook.original.DIRECTORY/('signal-send-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as stream: json.dump(report,stream,indent=2)
    print(json.dumps(dict(path=str(path),state=report['state'],reason=report.get('reason'),
                         send_attempted=report.get('send_attempted'),patch_restored=report.get('patch_restored'))))
    return 0 if report['state'] in ('preflight_verified','message_echo_observed','attempt_inspected','fixed_draft_cleared','draft_already_empty') else 1


if __name__=='__main__': raise SystemExit(main())
