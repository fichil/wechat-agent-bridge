"""Prepare diagnostics or inspect the bound input's runtime metadata without writes.

The live debugger route remains fail-closed while crash recovery is unverified.
This script does not implement any SetValue, Invoke or foreground operation.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/".local/static-tools"))
from wechat_agent_bridge.adapters.focus_diagnostics import DiagnosticLedger, require_native_recovery
from wechat_agent_bridge.console_models import ConsoleError
from test_debug_engine import helper_fingerprints

DIRECTORY = ROOT/".local/live-acceptance"


def prepare():
    evidence_path = DIRECTORY/"debugger-selftest.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.exists() else {}
    try:
        require_native_recovery(evidence, helper_fingerprints())
        readiness = "native_recovery_verified_coordinator_not_promoted"
    except ConsoleError as exc:
        readiness = str(exc)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    ledger = DiagnosticLedger(DIRECTORY/"diagnostics.sqlite3")
    try: status = ledger.status()
    finally: ledger.close()
    return {"diagnostic_readiness": readiness, "ledger": status,
            "write_enabled": False, "production_capabilities_changed": False}


def inspect(args):
    from wechat_agent_bridge.adapters.accessibility_gate import AccessibilityGate, NativeGate
    from wechat_agent_bridge.adapters.runtime_accessibility import ExactProfile, ReadOnlyProcess, BoundedMemory, inspect_binding
    from wechat_agent_bridge.adapters.windows import WindowsAdapter
    from wechat_agent_bridge.adapters.desktop_events import DesktopEventObserver
    from wechat_agent_bridge.adapters.background import snapshot, unchanged
    from wechat_agent_bridge.console_store import ConsoleStore
    from wechat_agent_bridge.protection import DPAPIProtector
    if not (args.hwnd and args.group and args.private and args.allow_accessibility_patch):
        raise ConsoleError("runtime_inspection_requires_explicit_window_and_targets")
    store = ConsoleStore(DIRECTORY/"gate.sqlite3", DPAPIProtector())
    native = reader = gate = adapter = None
    report = {"write_attempted": False, "debugger_attached": False}
    try:
        native = NativeGate(args.hwnd)
        fp = native.fingerprint()
        binding = store.get("binding", "account", {})
        account = binding.get("account", binding)
        if binding.get("fingerprint") != asdict(fp) or not account.get("identity_source"):
            raise ConsoleError("same_process_verified_binding_required")
        profile = ExactProfile(native.path.read_bytes())
        adapter = WindowsAdapter(timeout=8)
        gate = AccessibilityGate(native, allowed=True, save=lambda record:store.put("patch", "pending", record))
        if store.get("patch", "pending"):
            gate.recover(store.get("patch", "pending"))
        with DesktopEventObserver(fp.pid) as observer:
            before = snapshot(fp.pid)
            unchanged(before, before)
            observer.set_phase("runtime_metadata")
            targets = [args.group, args.private]
            target = {}
            def verify():
                target.update(adapter._call("target_control_probe", fp.pid, fp.hwnd, targets))
                return target.get("identity_verified") is True
            gate.enable(fp, verify)
            observer.check()
            reader = ReadOnlyProcess(fp, native.fingerprint)
            memory = BoundedMemory(reader.read, reader.check)
            report["runtime"] = inspect_binding(memory, fp, profile, target["input_runtime_id"])
            current = adapter._call("target_control_probe", fp.pid, fp.hwnd, targets)
            if any(current.get(key) != target.get(key) for key in ("target_index", "input_runtime_id", "session_runtime_id")):
                raise ConsoleError("runtime_target_changed")
            memory.recheck()
            observer.check()
            unchanged(before, snapshot(fp.pid))
            report["desktop_unchanged"] = True
            report["observation"] = observer.evidence.events
            report["fingerprint"] = asdict(fp)
    except ConsoleError as exc:
        report.pop("runtime", None)  # Do not retain partially validated metadata.
        report["error"] = str(exc)
    finally:
        try:
            if gate:
                gate.restore()
                report["patch_restored"] = gate.state == "restored"
        finally:
            if reader: reader.close()
            if adapter: adapter.close()
            if native: native.close()
            store.put("capability_evidence", "runtime_metadata", report)
            store.close()
    # Detailed metadata stays in the DPAPI-protected local store.
    result = {k:v for k,v in report.items() if k not in ("runtime", "fingerprint", "observation")}
    if "runtime" in report:
        result.update(runtime_binding=report["runtime"]["runtime_binding"],
                      input_class=report["runtime"]["input"]["class"],
                      input_connections=len(report["runtime"]["input_connections"]),
                      text_connections=len(report["runtime"]["text_connections"]))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--hwnd", type=int)
    parser.add_argument("--group")
    parser.add_argument("--private")
    parser.add_argument("--allow-accessibility-patch", action="store_true")
    args = parser.parse_args()
    if args.run:
        result = prepare()
        print(json.dumps(result, ensure_ascii=False))
        # No reserve/attach/write on a failed safety prerequisite. This boundary
        # is intentionally unconditional until crash recovery is implemented.
        return 2
    result = inspect(args) if args.inspect else prepare()
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
