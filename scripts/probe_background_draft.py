"""Operator-started, one-shot AutoSetFocus(False) draft experiment; never sends.

Default is preparation only. --preflight only reads the already bound target.
--run additionally requires --operator-idle and consumes the existing diagnostic
ledger before one fixed SetValue. No debugger, ETW, foreground repair or cleanup
write is used. A leftover draft is always for the operator to remove manually.
"""
import argparse
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.adapters.focus_diagnostics import (
    DiagnosticLedger, check_draft_target, fingerprint_digest,
)

DIRECTORY = ROOT / ".local/live-acceptance"
# Explicitly authorized by the operator on 2026-10-26 after the original one-use
# experiment was exhausted. This fixed case cannot be reset by changing a GUID.
TASKVIEW_LEDGER = "taskview-desktop2-20261026.sqlite3"
CLIENT_ORDER_TRIAL = 'client-order-20261026'


def ledger_file(taskview_desktop_id, *, approved_case=None):
    if approved_case is not None and (approved_case != CLIENT_ORDER_TRIAL or taskview_desktop_id is None):
        raise ConsoleError('unrecognized_approved_trial')
    if taskview_desktop_id is None:
        return DIRECTORY / "diagnostics.sqlite3"
    try:
        parsed = uuid.UUID(taskview_desktop_id)
        if not parsed.int or str(parsed) != taskview_desktop_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ConsoleError("taskview_desktop_id_required") from None
    return DIRECTORY / ('uia-client-order-20261026.sqlite3' if approved_case else TASKVIEW_LEDGER)


class RunLock:
    """Exclusive local ownership; a crash leaves evidence, never auto-takeover."""
    def __init__(self, path):
        self.path = path
        self.token = uuid.uuid4().hex
        self.owned = False

    def acquire(self):
        try:
            with self.path.open("x", encoding="ascii") as stream:
                stream.write(self.token)
                stream.flush()
                os.fsync(stream.fileno())
            self.owned = True
        except FileExistsError:
            raise ConsoleError("candidate_run_locked_or_recovery_required") from None

    def release(self):
        if self.owned:
            if self.path.read_text(encoding="ascii") != self.token:
                raise ConsoleError("candidate_run_lock_owner_changed")
            self.path.unlink()
            self.owned = False


def helper_fingerprints():
    names = ("scripts/probe_background_draft.py", "wechat_agent_bridge/adapters/uia_draft_candidate.py",
             "wechat_agent_bridge/adapters/uia_client.py", "wechat_agent_bridge/adapters/windows_uia.py",
             "wechat_agent_bridge/adapters/accessibility_gate.py", "wechat_agent_bridge/adapters/background.py",
             "wechat_agent_bridge/adapters/desktop_events.py", "wechat_agent_bridge/adapters/focus_diagnostics.py",
             "wechat_agent_bridge/adapters/virtual_desktop.py", "scripts/probe_taskview_draft.py",
             "scripts/uia_client_bootstrap.py", "scripts/uia_context_evidence.py", "scripts/uia_context_native.py")
    names += ('scripts/probe_client_order_draft.py',)
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


def validate_canary(report, fingerprints, dll_sha, os_version):
    from scripts.probe_uia_autofocus import complete_pass
    if (report.get("passed") is not True or not complete_pass(report) or
            report.get("fingerprints") != fingerprints or
            report.get("uia_sha256") != dll_sha or report.get("os") != os_version):
        raise ConsoleError("autofocus_synthetic_missing_or_stale")


def require_canary():
    from scripts.probe_uia_autofocus import fingerprints
    try:
        report = json.loads((DIRECTORY / "uia-autofocus-synthetic.json").read_text(encoding="utf-8"))
        if report.get("result", {}).get("comtypes_version") != importlib.metadata.version("comtypes"):
            raise ConsoleError("autofocus_synthetic_missing_or_stale")
        dll = Path(os.environ["SystemRoot"]) / "System32/UIAutomationCore.dll"
        sha = hashlib.sha256(dll.read_bytes()).hexdigest()
        validate_canary(report, fingerprints(), sha, platform.version())
    except (OSError, KeyError, ValueError):
        raise ConsoleError("autofocus_synthetic_missing_or_stale") from None
    return sha


def validate_binding(binding, evidence, account, fingerprint):
    identity = binding.get("account", {})
    if (not account or identity.get("wechat_id") != account or
            identity.get("identity_source") != "uia_own_profile_exact_id_user_selected_window" or
            binding.get("fingerprint") != fingerprint or
            evidence.get("patch", {}).get("fingerprint") != fingerprint):
        raise ConsoleError("same_process_verified_binding_required")
    target = evidence.get("target_controls", evidence.get("current_target", {}))
    if not target.get("identity_verified") or target.get("target_index") != 0:
        raise ConsoleError("previous_group_binding_required")
    for key in ("session_runtime_id", "input_runtime_id"):
        value = target.get(key)
        if not isinstance(value, list) or not 1 <= len(value) <= 16 or any(type(x) is not int for x in value):
            raise ConsoleError("previous_group_binding_required")
    return target


def _exchange(pipe, kind, value=None):
    pipe.send({"kind": kind, "value": value})
    if not pipe.poll(3):
        raise ConsoleError("candidate_controller_timeout")
    reply = pipe.recv()
    if reply != {"continue": True}:
        raise ConsoleError("candidate_stopped_by_controller")
    return True


def check_candidate_target(expected, current, written=False):
    if not written:
        check_draft_target(expected, current)
        return
    if (not current.get("identity_verified") or current.get("target_index") != 0 or
            current.get("value_readonly") is not False or current.get("draft_is_fixed_probe") is not True):
        raise ConsoleError("candidate_target_or_draft_changed")
    for key in ("session_runtime_id", "input_runtime_id"):
        if not expected.get(key) or current.get(key) != expected[key]:
            raise ConsoleError("candidate_target_or_draft_changed")


def candidate_worker(pipe, fp, names, expected, write):
    """Only this disposable process performs UIA; parent owns the patch/journal."""
    sys.coinit_flags = 0
    try:
        from scripts.uia_client_bootstrap import prepare_client_elements, read_pattern_context, require_safe_context
        # Pin the FALSE client's nodes before importing the legacy UIA reader.
        prepared = prepare_client_elements(fp, expected)
        from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
        from wechat_agent_bridge.adapters.uia_draft_candidate import DraftTarget, write_fixed_draft
        from wechat_agent_bridge.adapters.uia_draft_candidate import _empty, _identity
        backend = WindowsUIA()
        current = backend.target_control_probe(fp["pid"], fp["hwnd"], names)
        check_draft_target(expected, current)
        root = backend.instances[f'{fp["pid"]}:{fp["hwnd"]}'][0]
        root_runtime = list(root.element_info.runtime_id)
        client, requested_root, element, pattern, legacy_loaded, uia = prepared
        target = DraftTarget(fp["hwnd"], fp["pid"], tuple(root_runtime), tuple(current["input_runtime_id"]))
        _identity(requested_root, element, target, uia)
        _empty(pattern)
        context_checks = []
        def guard(raw, actual_element, actual_pattern):
            value = read_pattern_context(raw, actual_element, actual_pattern)
            require_safe_context(value)
            context_checks.append(value)
            return True
        guard(client._client, element, pattern)
        _exchange(pipe, "verified", {"target": current, "root_runtime_id": root_runtime})
        if not write:
            pipe.send({"kind": "result", "value": {"state": "preflight_verified", "attempted": False,
                "context_checks": context_checks, "pywinauto_loaded_before_first_client": legacy_loaded}})
            return

        def checkpoint(stage):
            # No UIA call after the final parent stop check; preceding candidate
            # reads already rechecked exact identity, draft and focus policy.
            if stage not in ("commit_write", "after_write"):
                value = backend.target_control_probe(fp["pid"], fp["hwnd"], names)
                check_candidate_target(expected, value, stage == "after_readback")
            return _exchange(pipe, "checkpoint", stage)

        result = write_fixed_draft(target, enabled=True, checkpoint=checkpoint,
                                   claim=lambda: _exchange(pipe, "claim"),
                                   client_factory=lambda: client._client, pattern_guard=guard)
        result.update(context_checks=context_checks, pywinauto_loaded_before_first_client=legacy_loaded)
        result["state"] = "readback_verified" if result["status"] == "readback_confirmed" else result["status"]
        pipe.send({"kind": "result", "value": result})
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ConsoleError) else "candidate_worker_failed"
        try:
            pipe.send({"kind": "error", "value": reason})
        except (OSError, EOFError):
            pass
    finally:
        pipe.close()


def run(args, *, taskview_desktop_id=None, inspection_worker=None, approved_case=None,current_desktop=False,target_scope=None):
    from wechat_agent_bridge.adapters.accessibility_gate import NativeGate, AccessibilityGate
    from wechat_agent_bridge.adapters.background import snapshot, unchanged
    from wechat_agent_bridge.adapters.desktop_events import DesktopEventObserver
    from wechat_agent_bridge.console_store import ConsoleStore
    from wechat_agent_bridge.protection import DPAPIProtector
    if inspection_worker is not None and (args.run or not args.preflight):
        raise ConsoleError('inspection_requires_readonly_preflight')
    if current_desktop and (inspection_worker is None or taskview_desktop_id is None):
        raise ConsoleError('current_desktop_requires_explicit_readonly_scope')
    if target_scope is not None and (inspection_worker is None or args.run or approved_case is not None):
        raise ConsoleError('private_scope_requires_readonly_inspection')
    if not (args.allow_accessibility_patch and args.account and args.group and args.private and args.target_evidence):
        raise ConsoleError("explicit_account_and_targets_required")
    if args.run and not args.operator_idle:
        raise ConsoleError("operator_started_idle_window_required")
    if args.group == args.private:
        raise ConsoleError("acceptance_target_ambiguous")
    if approved_case is not None and inspection_worker is not None:
        raise ConsoleError('approved_trial_requires_fixed_worker')
    selected_ledger = ledger_file(taskview_desktop_id, approved_case=approved_case)
    uia_sha = require_canary()
    evidence_path = Path(args.target_evidence).resolve()
    if evidence_path.parent != DIRECTORY.resolve() or evidence_path.stat().st_size > 1_000_000:
        raise ConsoleError("local_target_evidence_required")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
    store = ConsoleStore(DIRECTORY / "gate.sqlite3", DPAPIProtector())
    ledger = DiagnosticLedger(selected_ledger)
    native = gate = process = parent = remote = observer = None
    desktop_reader = desktop_binding = None
    request, claimed = None, False
    run_lock = RunLock(DIRECTORY / "autofocus-draft.lock")
    def current_sources():
        values = helper_fingerprints()
        if inspection_worker is not None:
            for name in ('scripts/probe_bound_uia_context.py', 'scripts/uia_context_evidence.py',
                         'scripts/uia_context_native.py', 'scripts/message_control_schema.py'):
                values[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        if target_scope is not None:
            for name in ('scripts/private_test_scope.py','scripts/contact_profile_identity.py','scripts/verify_contact_profile.py'):
                values[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        return values
    report = {"state": "failed", "attempted": False, "send_invoked": False,
              "api_called": False, "production_enabled": False, "debugger_attached": False,
              "uia_sha256": uia_sha, "os": platform.version(), "patch_restored": False,
              "fingerprints": current_sources(), "cleanup_errors": []}
    if taskview_desktop_id is not None:
        report["experiment"] = "operator_requested_taskview_desktop2_20261026"
        report["virtual_desktop_id"] = taskview_desktop_id
    if inspection_worker is not None:
        report['experiment'] = 'readonly_bound_uia_context'
    if approved_case is not None:
        report['experiment'] = approved_case
    started = time.monotonic()
    try:
        run_lock.acquire()
        if args.run and ledger.status()["attempt_used"]:
            raise ConsoleError("diagnostic_attempt_already_used")
        binding = store.get("binding", "account", {})
        stored_fp = binding.get("fingerprint", {})
        if target_scope is None:
            expected = validate_binding(binding, evidence, args.account, stored_fp)
            names=[args.group,args.private]
            report['target_kind']='group'
        else:
            expected=target_scope.resolve(binding,evidence,args.account,stored_fp,store,DIRECTORY)
            names=target_scope.names
            report.update(target_scope.metadata())
        if any(store.get(k,'pending') for k in ('patch','draft_probe','thread_hook','development_navigation','development_window_restore')):
            raise ConsoleError("prior_recovery_required")
        native = NativeGate(stored_fp["hwnd"])
        fp = native.fingerprint()
        if asdict(fp) != stored_fp:
            raise ConsoleError("same_process_verified_binding_required")
        baseline = snapshot(fp.pid)
        unchanged(baseline, baseline)
        if taskview_desktop_id is not None:
            from wechat_agent_bridge.adapters.virtual_desktop import (VirtualDesktopReader, validate_initial,
                unchanged_snapshot,validate_current_target,unchanged_current_target)
            desktop_reader = VirtualDesktopReader()
            target_desktop = dict(hwnd=fp.hwnd, **desktop_reader.query(fp.hwnd))
            if target_desktop["desktop_id"] != taskview_desktop_id:
                raise ConsoleError("taskview_target_desktop_changed")
            if current_desktop:
                desktop_binding=validate_current_target(target_desktop)
            else:
                front_desktop = dict(hwnd=baseline.foreground, **desktop_reader.query(baseline.foreground))
                desktop_binding = validate_initial(target_desktop, front_desktop)
            report["virtual_desktop_binding"] = desktop_binding
            report['desktop_scope']='current_desktop_background' if current_desktop else 'separated_desktops'
        observer = DesktopEventObserver(fp.pid).__enter__()

        def check():
            if time.monotonic() - started >= 45:
                raise ConsoleError("candidate_collection_deadline")
            if native.fingerprint() != fp:
                raise ConsoleError("candidate_process_identity_changed")
            if store.get("binding", "account", {}) != binding:
                raise ConsoleError("candidate_account_binding_changed")
            if target_scope is not None: target_scope.check(store)
            observer.check()
            current_snapshot = snapshot(fp.pid)
            unchanged(baseline, current_snapshot)
            if desktop_reader is not None:
                target_snapshot=dict(hwnd=fp.hwnd, **desktop_reader.query(fp.hwnd))
                if current_desktop:
                    unchanged_current_target(desktop_binding,target_snapshot)
                else:
                    unchanged_snapshot(desktop_binding,target_snapshot,
                        dict(hwnd=current_snapshot.foreground, **desktop_reader.query(current_snapshot.foreground)))
            return True

        gate = AccessibilityGate(native, allowed=True, save=lambda record: store.put("patch", "pending", record))
        # Controls are verified by the bounded worker immediately below. Failure
        # to expose the exact original target restores this one byte in finally.
        gate.enable(fp, check)
        context = mp.get_context("spawn")
        parent, remote = context.Pipe()
        process = context.Process(target=inspection_worker or candidate_worker,
            args=(remote, asdict(fp), names, expected, args.run))
        process.start()
        remote.close()
        verified = False
        while time.monotonic() - started < 45:
            check()
            if not parent.poll(0.03):
                if not process.is_alive():
                    raise ConsoleError("candidate_worker_disconnected")
                continue
            message = parent.recv()
            kind, value = message.get("kind"), message.get("value")
            if kind == "verified" and not verified:
                if inspection_worker is not None:
                    allowed = ('identity_verified', 'target_index', 'selected_row_verified',
                               'session_runtime_id', 'input_runtime_id', 'draft_empty', 'value_readonly',
                               'header_matches', 'selected_target_rows', 'input_label_length', 'reason')
                    allowed += ('header_class_counts','header_name_matches','session_candidate_counts','list_item_count')
                    report['observed_target_metadata'] = {k: value['target'][k] for k in allowed
                                                         if k in value['target']}
                check_draft_target(expected, value["target"])
                verified = True
                report["target_verified"] = True
                parent.send({"continue": True})
            elif kind == "checkpoint" and verified:
                observer.set_phase(str(value))
                parent.send({"continue": check()})
            elif kind == "claim" and verified and args.run and not claimed:
                check()
                request = ledger.reserve(fingerprint_digest(fp))
                ledger.consume_draft(request, fingerprint_digest(fp))
                claimed = True
                # Persist before releasing the worker. Missing response is
                # unknown: it cannot authorize another write after restart.
                report.update(attempted=True, state="unknown", draft_may_remain=True)
                parent.send({"continue": True})
            elif kind == "result" and verified:
                if not args.run and (value.get('attempted') is not False or
                                     value.get('state') == 'readback_verified'):
                    raise ConsoleError('inspection_result_claimed_write')
                if args.run and not claimed:
                    # Rejection before claim is allowed, never success.
                    if value.get("state") == "readback_verified" or value.get("attempted"):
                        raise ConsoleError("candidate_result_without_claim")
                report["worker"] = value
                report["attempted"] = bool(value.get("attempted"))
                report["state"] = value.get("state", "unknown" if claimed else "failed")
                until = time.monotonic() + 0.5
                while time.monotonic() < until:
                    check()
                    time.sleep(0.02)
                report["desktop_unchanged"] = True
                break
            elif kind == "error":
                if inspection_worker is not None and isinstance(message.get('details'),dict):
                    report['worker_failure_evidence']=message['details']
                raise ConsoleError(value)
            else:
                raise ConsoleError("candidate_protocol_mismatch")
        else:
            raise ConsoleError("candidate_collection_deadline")
    except KeyboardInterrupt:
        report.update(state="unknown" if claimed else "failed", reason="operator_cancelled")
    except Exception as exc:
        report["state"] = "unknown" if claimed else "failed"
        report["reason"] = str(exc) if isinstance(exc, ConsoleError) else "candidate_coordinator_failed"
    finally:
        if parent:
            try:
                parent.send({"continue": False})
            except (OSError, EOFError):
                pass
        try:
            if process and process.pid:
                process.join(1)
                if process.is_alive():
                    process.terminate()  # Only our multiprocessing child, never WeChat.
                    process.join(2)
                report["worker_exited"] = not process.is_alive()
                if process.exitcode != 0:
                    raise ConsoleError("candidate_worker_exit_unconfirmed")
            elif process:
                report["worker_exited"] = True  # start() never created a process.
        except Exception:
            report["state"] = "unknown" if claimed else "failed"
            report["cleanup_errors"].append("candidate_worker_exit_unconfirmed")
        try:
            if gate:
                restored = gate.restore()
                report["patch_restored"] = restored["state"] in ("restored", "unchanged")
                if not report["patch_restored"]:
                    report.update(state="unknown" if claimed else "failed", reason="candidate_process_exited_or_restore_unconfirmed")
        except Exception:
            report.update(state="recovery_required", reason="candidate_patch_restore_failed")
        if observer:
            try:
                observer.close()
            except Exception:
                observer.evidence.failed = True
            report["events_observed"] = len(observer.evidence.events)
            if taskview_desktop_id is not None:
                report["desktop_events"] = list(observer.evidence.events)
            if observer.evidence.failed or observer.evidence.events:
                if report["state"] != "recovery_required":
                    report["state"] = "unknown" if claimed else "failed"
                report["desktop_unchanged"] = False
        try:
            report["source_unchanged"] = current_sources() == report["fingerprints"]
        except Exception:
            report["source_unchanged"] = False
            report["cleanup_errors"].append("candidate_source_check_failed")
        if not report["source_unchanged"] and report["state"] != "recovery_required":
            report.update(state="unknown" if claimed else "failed", reason="candidate_source_changed")
        if desktop_reader is not None:
            try:
                final_snapshot = snapshot(fp.pid)
                report["virtual_desktop_final"] = {"target": dict(hwnd=fp.hwnd, **desktop_reader.query(fp.hwnd))}
                if current_desktop:
                    report['virtual_desktop_final']['foreground_hwnd']=final_snapshot.foreground
                else:
                    report['virtual_desktop_final']['foreground']=dict(hwnd=final_snapshot.foreground,
                        **desktop_reader.query(final_snapshot.foreground))
            except Exception:
                report["virtual_desktop_final_unavailable"] = True
            try:
                desktop_reader.close()
                report["virtual_desktop_reader_closed"] = True
            except Exception:
                report["cleanup_errors"].append("taskview_reader_close_failed")
                if report["state"] != "recovery_required":
                    report["state"] = "unknown" if claimed else "failed"
        if request:
            try:
                ledger.finish(request, "recovery_required" if not report["patch_restored"] else
                              "finished" if report["state"] == "readback_verified" else "failed")
            except Exception:
                report["cleanup_errors"].append("candidate_ledger_finish_failed")
        for pipe in (parent, remote):
            if pipe:
                try:
                    pipe.close()
                except Exception:
                    report["cleanup_errors"].append("candidate_pipe_close_failed")
        if native:
            try:
                native.close()
            except Exception:
                report["cleanup_errors"].append("candidate_native_close_failed")
        try:
            if not store.get("patch", "pending"):
                run_lock.release()
            elif run_lock.owned:
                report["cleanup_errors"].append("candidate_lock_retained_for_recovery")
        except Exception:
            report.update(state="recovery_required", reason="candidate_run_lock_release_failed")
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if report["cleanup_errors"] and report["state"] != "recovery_required":
            report["state"] = "unknown" if claimed else "failed"
        try:
            report["ledger"] = ledger.status()
            evidence_key = ('client_order_draft' if approved_case is not None else
                'readonly_bound_uia_context' if inspection_worker is not None else
                'taskview_desktop2_draft' if taskview_desktop_id is not None else 'autofocus_draft_candidate')
            store.put("capability_evidence", evidence_key, report)
        except Exception:
            report["cleanup_errors"].append("candidate_audit_write_failed")
            if report["state"] != "recovery_required":
                report["state"] = "unknown" if claimed else "failed"
        finally:
            for label, resource in (("ledger", ledger), ("store", store)):
                try:
                    resource.close()
                except Exception:
                    report["cleanup_errors"].append("candidate_" + label + "_close_failed")
                    if report["state"] != "recovery_required":
                        report["state"] = "unknown" if claimed else "failed"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--preflight", action="store_true")
    parser.add_argument("--operator-idle", action="store_true")
    parser.add_argument("--allow-accessibility-patch", action="store_true")
    parser.add_argument("--account")
    parser.add_argument("--group")
    parser.add_argument("--private")
    parser.add_argument("--target-evidence")
    args = parser.parse_args(argv)
    try:
        if not (args.run or args.preflight):
            require_canary()
            report = {"state": "prepared", "write_enabled": False, "send_enabled": False,
                      "requires_operator_idle_start": True}
        else:
            report = run(args)
    except Exception as exc:
        report = {"state": "failed", "reason": str(exc) if isinstance(exc, ConsoleError) else "candidate_preparation_failed",
                  "write_enabled": False, "send_enabled": False}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["state"] in ("prepared", "preflight_verified", "readback_verified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
