"""Explicit one-window gate experiment. Always restores before returning.

Only structural control metadata is emitted. No chats, names, values, actions,
model calls, system accessibility flags, disk patches or clipboard operations.
"""
import argparse
import json
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechat_agent_bridge.adapters.accessibility_gate import AccessibilityGate, NativeGate
from wechat_agent_bridge.adapters.windows import WindowsAdapter
from wechat_agent_bridge.console_store import ConsoleStore
from wechat_agent_bridge.protection import DPAPIProtector
from wechat_agent_bridge.console_models import ConsoleError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--allow-accessibility-patch", action="store_true", required=True)
    parser.add_argument("--observe-profile-seconds", type=int, default=0)
    parser.add_argument("--expected-wechat-id")
    parser.add_argument("--group")
    parser.add_argument("--private")
    parser.add_argument("--target-controls-only", action="store_true")
    parser.add_argument("--semantic-candidates", action="store_true", help="只读查询替代接口，要求同一进程已核验绑定；不写草稿")
    parser.add_argument("--draft-probe", action="store_true", help="仅设置固定验证文本并回读/清理；绝不调用发送")
    parser.add_argument("--reconcile-cleared-draft", action="store_true", help="用户手动清理后只读确认；不清除微信草稿")
    args = parser.parse_args()
    if args.semantic_candidates and (args.draft_probe or args.target_controls_only or args.expected_wechat_id):
        parser.error("semantic_candidates_is_metadata_only")
    if args.draft_probe and not (args.expected_wechat_id and args.group and args.private):
        parser.error("draft_probe_requires_exact_account_and_both_targets")
    directory = Path(__file__).resolve().parents[1]/".local/live-acceptance"
    directory.mkdir(parents=True, exist_ok=True)
    store = ConsoleStore(directory/"gate.sqlite3", DPAPIProtector())
    if args.draft_probe and store.get("capability_evidence", "background", {}).get("set_value") == "blocked_focus_changed":
        store.close()
        parser.error("background_write_changes_focus: blocked; a new implementation and review are required")
    native = NativeGate(args.hwnd)
    adapter = WindowsAdapter(timeout=15)
    gate = AccessibilityGate(native, allowed=args.allow_accessibility_patch,
                             save=lambda record: store.put("patch", "pending", record))
    result = {"scope": "selected_window_structural_metadata_only"}
    try:
        pending = store.get("patch", "pending")
        if pending:
            result["recovery"] = gate.recover(pending)
        fp = native.fingerprint()
        if args.semantic_candidates:
            from dataclasses import asdict
            binding = store.get("binding", "account", {})
            account = binding.get("account", binding)
            if (binding.get("fingerprint") != asdict(fp) or not account.get("identity_source")):
                raise ConsoleError("same_process_verified_binding_required")
        result["before"] = adapter._call("background_structure", fp.pid, fp.hwnd)
        def verify():
            result["after"] = adapter._call("background_structure", fp.pid, fp.hwnd)
            return result["after"]["controls_exposed"]
        result["patch"] = gate.enable(fp, verify)
        if args.semantic_candidates:
            from wechat_agent_bridge.adapters.desktop_events import DesktopEventObserver
            from wechat_agent_bridge.adapters.background import snapshot, unchanged
            with DesktopEventObserver(fp.pid) as observer:
                observer.set_phase("semantic_inventory")
                try:
                    before = snapshot(fp.pid)
                    unchanged(before, before)
                    result["semantic_candidates"] = adapter._call("semantic_candidates", fp.pid, fp.hwnd)
                    observer.check()
                    unchanged(before, snapshot(fp.pid))
                finally:
                    observer.close()
                    result["observation"] = {"failed": observer.evidence.failed, "events": observer.evidence.events}
            store.put("capability_evidence", "semantic_candidates", {
                "fingerprint": result["patch"]["fingerprint"],
                "report": result["semantic_candidates"], "observation": result["observation"]})
        if args.target_controls_only and args.group and args.private:
            result["target_controls"] = adapter._call("target_control_probe", fp.pid, fp.hwnd, [args.group, args.private])
            if args.reconcile_cleared_draft:
                pending = store.get("draft_probe", "pending")
                if (pending and pending["fingerprint"] == result["patch"]["fingerprint"]
                        and result["target_controls"].get("identity_verified") is True
                        and result["target_controls"].get("target_index") == 0
                        and result["target_controls"].get("draft_empty") is True):
                    store.put("draft_probe", "pending", None)
                    result["manual_draft_cleanup_confirmed"] = True
        if args.expected_wechat_id:
            until = time.monotonic()+(45 if args.draft_probe else 0)
            while True:
                try:
                    account = adapter._call("verify_own_profile", fp.pid, fp.hwnd, args.expected_wechat_id)
                    break
                except ConsoleError as exc:
                    if str(exc) != "account_identity_uncertain_open_own_profile" or time.monotonic() >= until:
                        raise
                    time.sleep(1)
            result["profile_identity"] = {"matched": True, "source": account["identity_source"]}
            store.put("binding", "account", {"account": account, "fingerprint": result["patch"]["fingerprint"]})
            if args.group and args.private:
                result["current_target"] = adapter._call("current_target_metadata", account["id"], [args.group, args.private])
            if args.draft_probe:
                from wechat_agent_bridge.adapters.background import snapshot
                from wechat_agent_bridge.acceptance import FIXED_TEXT
                until = time.monotonic()+40
                while snapshot(fp.pid).target_active and time.monotonic() < until:
                    time.sleep(1)
                if native.fingerprint() != fp:
                    raise RuntimeError("process_changed")
                store.put("draft_probe", "pending", {"fingerprint": result["patch"]["fingerprint"], "text": FIXED_TEXT})
                result["draft_probe"] = adapter._call("probe_background_draft", account["id"], [args.group, args.private], FIXED_TEXT)
                if result["draft_probe"]["draft_restored"]:
                    store.put("draft_probe", "pending", None)
        if args.observe_profile_seconds:
            result["profile_windows"] = []
            until = time.monotonic()+min(max(args.observe_profile_seconds, 0), 50)
            seen = set()
            while time.monotonic() < until:
                metadata = adapter._call("profile_window_metadata", fp.pid, fp.hwnd)
                encoded = json.dumps(metadata, sort_keys=True)
                if encoded not in seen:
                    result["profile_windows"].append(metadata)
                    seen.add(encoded)
                time.sleep(1)
    finally:
        try:
            result["restore"] = gate.restore()
        finally:
            adapter.close(); native.close(); store.close()
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            evidence = directory/("structure-"+stamp+".json")
            evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            for key in ("before", "after"):
                if key in result:
                    structure = result[key]
                    from collections import Counter
                    structure["types"] = dict(Counter(r["type"] for r in structure["controls"]))
                    if not args.semantic_candidates:
                        structure["interesting"] = [r for r in structure["controls"] if r["type"] in ("Edit", "List", "ListItem", "Document") or "Profile" in r["class"]]
                    del structure["controls"]
            result["evidence"] = str(evidence)
            print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
