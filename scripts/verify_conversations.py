"""Explicit two-account live HTTP-contract acceptance without operating WeChat UI.

Only counts, reason codes, timings and observer counters are saved. Test settings
use a temporary local file, never the user's saved preferences. No raw payloads,
names, account IDs, images, source identifiers or database keys enter the report.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--expected-accounts", type=int, default=2)
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return 0
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    from backend.app.conversation_models import Exclusions
    from wechat_account_probe.native import enumerate_instances
    from wechat_account_probe.observation import Observation
    import psutil

    directory = ROOT / ".local" / "conversation-acceptance"
    directory.mkdir(parents=True, exist_ok=True)
    report = dict(completed=False, expected_accounts=args.expected_accounts, runs=[])
    owned = []
    observer = Observation([i.pid for i in enumerate_instances()[0]])
    try:
        with tempfile.TemporaryDirectory(dir=directory) as temporary, observer:
            with TestClient(create_app(settings_path=Path(temporary) / "settings.json", monitor=False),
                            base_url="http://127.0.0.1:8765") as client:
                scan = client.post("/api/v1/account-scans", json={}).json()
                deadline = time.monotonic() + 190
                while scan["status"] == "running" and time.monotonic() < deadline:
                    time.sleep(.5)
                    scan = client.post('/api/v1/account-scans/query', json={'scan_id': scan['scan_id']}).json()
                snapshot = client.post('/api/v1/accounts/query', json={}).json()
                accounts = snapshot["accounts"]
                report.update(account_count=len(accounts), account_coverage=snapshot["summary"]["coverage_complete"],
                              versions=sorted({i["version"] for a in accounts for i in a["instances"]}))
                assert len(accounts) == args.expected_accounts and snapshot["exit_code"] == 0, "account_acceptance_failed"
                print(json.dumps(dict(stage="accounts_ready", count=len(accounts))), flush=True)

                def post(path, account_id, **values):
                    response = client.post("/api/v1/" + path, json=dict(account_id=account_id, **values))
                    assert response.status_code in (200, 202), "api_request_failed"
                    assert response.headers["cache-control"] == "no-store", "cache_header_missing"
                    return response.json()

                def refresh(account_id):
                    begin = time.monotonic()
                    task = post("conversation-refreshes", account_id)
                    deadline = time.monotonic() + 70
                    while task["status"] in ("queued", "running") and time.monotonic() < deadline:
                        time.sleep(.2)
                        task = post("conversation-refreshes/query", account_id, refresh_id=task["refresh_id"])
                    assert task["status"] == "completed", task.get("error") or "refresh_not_completed"
                    return round(time.monotonic() - begin, 3)

                def collect(account_id):
                    page = post("conversations/query", account_id, page_size=100)
                    result = list(page["items"])
                    original = page["snapshot_id"]
                    while page["next_cursor"]:
                        page = post("conversations/query", account_id, page_size=100, cursor=page["next_cursor"])
                        assert page["snapshot_id"] == original, "snapshot_changed_during_paging"
                        result.extend(page["items"])
                    assert len(result) == page["total"], "pagination_incomplete"
                    assert len({r["conversation_id"] for r in result}) == len(result), "pagination_duplicate"
                    return result, page

                for index, account in enumerate(accounts):
                    account_id = account["account_id"]
                    cold = refresh(account_id)
                    defaults, _ = collect(account_id)
                    assert not any(r["category"] in Exclusions.model_fields for r in defaults), "default_filter_failed"
                    post("conversation-settings/update", account_id, exclusions={k: False for k in Exclusions.model_fields})
                    all_items, page = collect(account_id)
                    categories = Counter(r["category"] for r in all_items)
                    assert all(r["is_container"] for r in all_items if r["category"] in
                               ("service_accounts", "official_accounts", "folded_chats")), "container_flattened"
                    for category in Exclusions.model_fields:
                        rules = {k: k != category for k in Exclusions.model_fields}
                        post("conversation-settings/update", account_id, exclusions=rules)
                        filtered, _ = collect(account_id)
                        assert len(filtered) == len(defaults) + categories[category], "filter_count_mismatch"
                    post("conversation-settings/update", account_id, exclusions=Exclusions().model_dump())
                    warm = refresh(account_id)
                    current, _ = collect(account_id)
                    common = set(r["conversation_id"] for r in defaults) & set(r["conversation_id"] for r in current)
                    assert common or not defaults, "conversation_ids_unstable"
                    workers = client.app.state.conversations.reader._workers.values()
                    for worker in workers:
                        if worker.process.pid not in [p[0] for p in owned]:
                            proc = psutil.Process(worker.process.pid)
                            owned.append((proc.pid, proc.create_time()))
                    row = dict(account_index=index, cold_seconds=cold, warm_seconds=warm,
                               total=page["total"], default_visible=len(defaults), categories=dict(categories),
                               avatar_count=sum(bool(r["avatar"]) for r in all_items),
                               status=page["status"], issues=page["issues"],
                               category_samples={k: categories[k] > 0 for k in Exclusions.model_fields})
                    report["runs"].append(row)
                    print(json.dumps(dict(stage="account_verified", **row)), flush=True)
                report["completed"] = True
    except Exception as exc:
        # Assertion messages above are constants/reason codes, never raw data.
        report["error"] = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
        report["completed"] = False
    finally:
        report["observation"] = observer.result
        report["workers_cleaned"] = all(not psutil.pid_exists(pid) or
            psutil.Process(pid).create_time() != created for pid, created in owned)
        report["noninterference"] = ("zero_wechat_focus_events" if observer.result.get("available") and
            not observer.result["wechat_foreground_events"] and not observer.result["wechat_focus_events"] else "inconclusive")
        report["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
        destination = directory / ("live-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(dict(report=str(destination), completed=report["completed"],
                             workers_cleaned=report["workers_cleaned"], noninterference=report["noninterference"])), flush=True)
    return 0 if report["completed"] and report["workers_cleaned"] and report["noninterference"] == "zero_wechat_focus_events" else 2


if __name__ == "__main__":
    sys.exit(main())
