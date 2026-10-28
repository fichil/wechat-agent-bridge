"""Opt-in native readiness wait before the unchanged strict sender binder.

Observes only visible HWND sets of the exact process and input timestamps.
A new window is a readiness signal, never identity evidence. No patch, UIA,
text, screenshot, activation or clipboard operation runs during the wait.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from wechat_agent_bridge.console_models import ConsoleError


def wait_for_window(handles, tick, check, main_hwnd, *, clock=time.monotonic, sleep=time.sleep,
                    timeout=60, stable_seconds=3):
    if not 0 < stable_seconds < timeout <= 60:
        raise ConsoleError('profile_wait_invalid_bounds')
    check()
    baseline = frozenset(handles())
    if main_hwnd not in baseline:
        raise ConsoleError('profile_wait_main_missing')
    started = clock()
    candidate, stable, last_tick = None, started, tick()
    while clock()-started < timeout:
        check()
        current = frozenset(handles())
        if main_hwnd not in current:
            raise ConsoleError('profile_wait_main_missing')
        added = current-baseline
        if len(added)>1:
            raise ConsoleError('profile_wait_new_window_ambiguous')
        new_candidate = next(iter(added)) if added else None
        now_tick = tick()
        if new_candidate != candidate or now_tick != last_tick:
            candidate, last_tick, stable = new_candidate, now_tick, clock()
        if candidate is not None and clock()-stable >= stable_seconds:
            check()
            if frozenset(handles()) != current or tick() != last_tick:
                raise ConsoleError('profile_wait_state_changed_at_handoff')
            return dict(state='window_ready_not_identity',candidate_hwnd=candidate,
                        elapsed_seconds=round(clock()-started,3),identity_verified=False)
        sleep(.1)
    raise ConsoleError('profile_wait_timeout')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--wait-and-bind',action='store_true')
    parser.add_argument('--allow-accessibility-patch',action='store_true')
    for field in ('account','fingerprint-evidence','cua-evidence'):
        parser.add_argument('--'+field)
    args=parser.parse_args(argv)
    if not args.wait_and_bind:
        print(json.dumps(dict(state='disabled',bind_invoked=False)))
        return 0
    from scripts.rebind_sender_profile import require_receipts,original
    from scripts.development_window_restore import load_local_hashed
    from scripts.development_navigation import input_tick
    from wechat_agent_bridge.adapters.accessibility_gate import NativeGate
    from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
    from wechat_agent_bridge.adapters.background import snapshot
    report=dict(kind='sender_profile_readiness',binder_call_planned=False,identity_verified=False,
                patch_enabled_during_wait=False,ui_text_read_during_wait=False)
    native=None
    try:
        if not args.allow_accessibility_patch:
            raise ConsoleError('profile_wait_explicit_binder_patch_opt_in_required')
        evidence,fp_sha=load_local_hashed(args.fingerprint_evidence)
        cua,cua_sha=load_local_hashed(args.cua_evidence)
        fp=require_receipts(evidence,fp_sha,cua,args.account)
        report.update(fingerprint=asdict(fp),cua_evidence_sha256=cua_sha,fingerprint_evidence_sha256=fp_sha)
        native=NativeGate(fp.hwnd)
        source=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        def check():
            if (original.DIRECTORY/'STOP').exists():
                raise ConsoleError('profile_wait_emergency_stop')
            if native.fingerprint()!=fp or native.read()!=0:
                raise ConsoleError('profile_wait_process_or_patch_changed')
            if snapshot(fp.pid).locked:
                raise ConsoleError('desktop_locked_or_unavailable')
            if (hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=source or
                    load_local_hashed(args.fingerprint_evidence)[1]!=fp_sha or
                    load_local_hashed(args.cua_evidence)[1]!=cua_sha):
                raise ConsoleError('profile_wait_sources_changed')
        print(json.dumps(dict(state='waiting_for_user_profile_window',max_seconds=60,
                              no_ui_actions=True)),flush=True)
        result=wait_for_window(lambda:WindowsUIA._visible_process_window_handles(fp.pid),
                               input_tick,check,fp.hwnd)
        report.update(result,binder_call_planned=True)
    except Exception as exc:
        report.update(state='failed',reason=str(exc) if isinstance(exc,ConsoleError) else 'profile_wait_failed')
    finally:
        if native:
            native.close()
    path=original.DIRECTORY/('sender-profile-readiness-'+uuid.uuid4().hex+'.json')
    raw=(json.dumps(report,indent=2)+'\n').encode()
    with path.open('xb') as stream:stream.write(raw)
    print(json.dumps(dict(path=str(path),state=report['state'],sha256=hashlib.sha256(raw).hexdigest())),flush=True)
    if report['state']!='window_ready_not_identity':return 1
    # Exactly one invocation; the binder retains all existing identity, input,
    # source, recovery and transaction guards. No budget reset or retry loop.
    command=[sys.executable,'-B','-X','utf8',str(ROOT/'scripts/rebind_sender_profile.py'),
             '--bind-current-profile','--allow-accessibility-patch','--account',args.account,
             '--fingerprint-evidence',args.fingerprint_evidence,'--cua-evidence',args.cua_evidence]
    return subprocess.run(command,cwd=ROOT).returncode


if __name__=='__main__':raise SystemExit(main())
