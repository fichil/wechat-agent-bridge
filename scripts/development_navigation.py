"""Explicit development-only focus of an already bound empty test input.

This is foreground assistance, never a background compatibility experiment.
No keys, mouse, clipboard, text mutation, send, login or process helper exists.
It is intentionally separate from the production Windows adapter.
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
from wechat_agent_bridge.console_models import ConsoleError


def activate_transaction(port,checkpoint,claim,*,development_foreground=False):
    attempted=False
    try:
        if not development_foreground: raise ConsoleError('development_foreground_opt_in_required')
        checkpoint(); port.verify_target()
        if not port.empty(): raise ConsoleError('development_existing_draft_or_unknown')
        if port.active(): return dict(state='already_active',attempted=False,background_qualified=False)
        claim()
        checkpoint(); port.verify_target()
        if not port.empty(): raise ConsoleError('development_existing_draft_or_unknown')
        attempted=True
        port.focus_bound_input()  # One public SetFocus call, no fallback.
        checkpoint(); port.verify_target()
        if not port.empty(): raise ConsoleError('development_draft_changed')
        if not port.active(): raise ConsoleError('development_activation_not_observed')
        return dict(state='activated_for_development',attempted=True,background_qualified=False)
    except Exception as exc:
        return dict(state='unknown' if attempted else 'failed',attempted=attempted,background_qualified=False,
                    reason=str(exc) if isinstance(exc,ConsoleError) else 'development_activation_failed')


def empty_draft(pattern):
    readonly=pattern.CurrentIsReadOnly
    return type(readonly) in (bool,int) and readonly==0 and pattern.CurrentValue==''


def worker(pipe,fp,names,expected):
    sys.coinit_flags=0
    try:
        from scripts.uia_client_bootstrap import prepare_client_elements,read_pattern_context,require_safe_context
        prepared=prepare_client_elements(fp,expected)
        from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
        from wechat_agent_bridge.adapters.background import snapshot
        client,root,element,pattern,legacy,uia=prepared
        if legacy: raise ConsoleError('development_client_order_unverified')
        backend=WindowsUIA()
        class Port:
            def verify_target(self):
                client.require_background_mode()
                if root.CurrentProcessId!=fp['pid'] or root.CurrentNativeWindowHandle!=fp['hwnd']:
                    raise ConsoleError('development_root_changed')
                original.check_draft_target(expected,backend.target_control_probe(fp['pid'],fp['hwnd'],names))
                if tuple(element.GetRuntimeId())!=tuple(expected['input_runtime_id']):
                    raise ConsoleError('development_input_changed')
                require_safe_context(read_pattern_context(client._client,element,pattern))
            def empty(self): return empty_draft(pattern)
            def active(self):
                state=snapshot(fp['pid'])
                return not state.locked and state.foreground==fp['hwnd']
            def focus_bound_input(self): element.SetFocus()
        result=activate_transaction(Port(),lambda:original._exchange(pipe,'checkpoint'),
            lambda:original._exchange(pipe,'claim'),development_foreground=True)
        pipe.send(dict(kind='result',value=result))
    except Exception as exc:
        pipe.send(dict(kind='error',value=str(exc) if isinstance(exc,ConsoleError) else 'development_worker_failed'))
    finally: pipe.close()


def cleanup_resources(report,*,process=None,gate=None,observer=None,resources=()):
    """Best-effort cleanup; one failure must not skip restoration or handle close."""
    errors=report['cleanup_errors']
    if process is not None:
        try:
            if process.pid:
                process.join(1)
                if process.is_alive(): process.terminate(); process.join(2)
                report['worker_exited']=not process.is_alive()
                if not report['worker_exited'] or process.exitcode!=0:
                    errors.append('development_worker_exit_unconfirmed')
                if report['worker_exited']: process.close()
        except Exception: errors.append('development_worker_cleanup_failed')
    if gate is not None:
        try: report['patch_restored']=gate.restore()['state'] in ('restored','unchanged','process_exited')
        except Exception: errors.append('development_patch_restore_failed')
    if observer is not None:
        try:
            observer.close()
            with observer.evidence.lock:
                report['events']=list(observer.evidence.events)
                if observer.evidence.failed: errors.append('development_observer_failed')
        except Exception: errors.append('development_observer_cleanup_failed')
    for label,resource in resources:
        if resource is not None:
            try: resource.close()
            except Exception: errors.append(label+'_close_failed')
    if errors or (gate is not None and not report.get('patch_restored')):
        report['state']='recovery_required'


def input_tick():
    import ctypes as c
    from ctypes import wintypes as w
    class Info(c.Structure): _fields_=[('size',w.UINT),('tick',w.DWORD)]
    value=Info(c.sizeof(Info),0); user=c.WinDLL('user32',use_last_error=True)
    user.GetLastInputInfo.argtypes=[c.POINTER(Info)]
    if not user.GetLastInputInfo(c.byref(value)): raise ConsoleError('development_input_clock_unavailable')
    return value.tick


def run(args):
    from wechat_agent_bridge.adapters.accessibility_gate import NativeGate,AccessibilityGate
    from wechat_agent_bridge.adapters.background import snapshot
    from wechat_agent_bridge.adapters.desktop_events import DesktopEventObserver
    from wechat_agent_bridge.adapters.virtual_desktop import VirtualDesktopReader
    from wechat_agent_bridge.console_store import ConsoleStore
    from wechat_agent_bridge.protection import DPAPIProtector
    from wechat_agent_bridge.adapters.windows_uia import VERIFIED_READ_PROFILES,VERIFIED_SEND_PROFILES
    if not (args.development_foreground and args.allow_accessibility_patch and args.account and args.group and
            args.private and args.target_evidence and args.synthetic_evidence and args.virtual_desktop):
        raise ConsoleError('development_exact_scope_and_opt_in_required')
    from scripts.probe_bound_uia_context import require_context_canary
    require_context_canary(args.synthetic_evidence)
    path=Path(args.target_evidence).resolve()
    if path.parent!=original.DIRECTORY.resolve() or path.stat().st_size>1000000:
        raise ConsoleError('development_local_target_evidence_required')
    evidence=json.loads(path.read_text(encoding='utf-8-sig'))
    store=ConsoleStore(original.DIRECTORY/'gate.sqlite3',DPAPIProtector())
    lock=original.RunLock(original.DIRECTORY/'autofocus-draft.lock')
    native=gate=observer=desktop=process=parent=child=None
    request='development-focus-'+uuid.uuid4().hex; claimed=False
    report=dict(kind='development_foreground_navigation',state='failed',attempted=False,background_qualified=False,
                send_invoked=False,api_called=False,clipboard_written=False,cleanup_errors=[])
    report['target_evidence_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    started=time.monotonic()
    try:
        lock.acquire()
        binding=store.get('binding','account',{})
        expected=original.validate_binding(binding,evidence,args.account,binding.get('fingerprint',{}))
        if any(store.get(kind,'pending') for kind in ('patch','thread_hook','draft_probe','development_navigation')):
            raise ConsoleError('development_prior_recovery_required')
        native=NativeGate(binding['fingerprint']['hwnd']); fp=native.fingerprint()
        if asdict(fp)!=binding['fingerprint']: raise ConsoleError('development_process_changed')
        if fp.version in VERIFIED_READ_PROFILES or fp.version in VERIFIED_SEND_PROFILES:
            raise ConsoleError('development_requires_production_adapter_disabled')
        baseline=snapshot(fp.pid); tick=input_tick()
        report['fingerprint']=asdict(fp)
        desktop=VirtualDesktopReader()
        if desktop.query(fp.hwnd)['desktop_id']!=args.virtual_desktop:
            raise ConsoleError('development_bound_desktop_changed')
        report['initial_desktop']=desktop.query(fp.hwnd)
        observer=DesktopEventObserver(fp.pid).__enter__()
        def check():
            if (original.DIRECTORY/'STOP').exists(): raise ConsoleError('development_emergency_stop')
            if time.monotonic()-started>25: raise ConsoleError('development_deadline')
            if native.fingerprint()!=fp or store.get('binding','account',{})!=binding:
                raise ConsoleError('development_binding_changed')
            now=snapshot(fp.pid)
            if now.locked: raise ConsoleError('desktop_locked_or_unavailable')
            if input_tick()!=tick: raise ConsoleError('development_human_takeover')
            if now.clipboard_sequence!=baseline.clipboard_sequence: raise ConsoleError('development_clipboard_changed')
            if observer.evidence.failed: raise ConsoleError('development_observer_failed')
            if not claimed and now.foreground!=baseline.foreground: raise ConsoleError('development_foreground_changed_before_claim')
            return True
        check()
        gate=AccessibilityGate(native,allowed=True,save=lambda record:store.put('patch','pending',record))
        report['patch']=gate.enable(fp,check)
        context=mp.get_context('spawn'); parent,child=context.Pipe()
        process=context.Process(target=worker,args=(child,asdict(fp),[args.group,args.private],expected))
        process.start(); child.close()
        while True:
            check()
            if not parent.poll(.03):
                if not process.is_alive(): raise ConsoleError('development_worker_exited')
                continue
            item=parent.recv(); kind=item.get('kind')
            if kind=='checkpoint': parent.send({'continue':check()})
            elif kind=='claim' and not claimed:
                check(); claimed=True
                report.update(state='unknown',attempted=True)
                store.put('development_navigation','pending',dict(request=request,fingerprint=asdict(fp),method='bound_input_SetFocus'))
                store.put('development_navigation',request,report)
                observer.set_phase('explicit_development_focus')
                parent.send({'continue':True})
            elif kind=='result':
                result=item['value']
                if result.get('attempted') and not claimed: raise ConsoleError('development_result_without_claim')
                report.update(result); report['worker']=result
                report['final_desktop']=desktop.query(fp.hwnd)
                break
            elif kind=='error': raise ConsoleError(item['value'])
            else: raise ConsoleError('development_protocol_invalid')
    except Exception as exc:
        report.update(state='unknown' if claimed else 'failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'development_navigation_failed')
    finally:
        if parent:
            try: parent.send({'continue':False})
            except (OSError,EOFError): pass
        cleanup_resources(report,process=process,gate=gate,observer=observer,
            resources=(('parent',parent),('child',child),('desktop',desktop),('native',native)))
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        try:
            store.put('development_navigation',request,report)
            if claimed and report['state'] in ('activated_for_development','already_active','failed') and report.get('patch_restored'):
                store.put('development_navigation','pending',None)
            if not store.get('patch','pending'): lock.release()
        finally: store.close()
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--activate-bound-window',action='store_true')
    parser.add_argument('--development-foreground',action='store_true')
    parser.add_argument('--allow-accessibility-patch',action='store_true')
    for flag in ('account','group','private','target-evidence','synthetic-evidence','virtual-desktop'): parser.add_argument('--'+flag)
    args=parser.parse_args(argv)
    if not args.activate_bound_window:
        print(json.dumps(dict(state='disabled',background_qualified=False))); return 0
    try: report=run(args)
    except Exception as exc: report=dict(state='failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'development_preparation_failed')
    report['source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    path=original.DIRECTORY/('development-navigation-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as stream: json.dump(report,stream,indent=2)
    print(json.dumps(dict(path=str(path),state=report['state'],reason=report.get('reason'),patch_restored=report.get('patch_restored'))))
    return 0 if report['state'] in ('activated_for_development','already_active') else 1


if __name__=='__main__': raise SystemExit(main())
