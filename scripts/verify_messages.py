"""Explicit read-only live acceptance; only aggregate metrics leave RAM."""
import argparse
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
        parser.print_help(); return 0
    import psutil
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    from wechat_account_probe.native import enumerate_instances
    from wechat_account_probe.observation import Observation
    psutil.Process().nice(psutil.IDLE_PRIORITY_CLASS)
    observer = Observation([i.pid for i in enumerate_instances()[0]])
    report = dict(completed=False, runs=[], sample_counts={}, coverage_counts={}, versions=[])
    owned = []
    output = ROOT / ".local" / "message-acceptance"
    output.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=output) as temporary, observer:
            with TestClient(create_app(settings_path=Path(temporary)/"settings.json", monitor=False), base_url="http://127.0.0.1:8765") as client:
                scan = client.post("/api/v1/account-scans", json={}).json()
                deadline = time.monotonic()+200
                while scan["status"] == "running":
                    assert time.monotonic() < deadline, "scan_timeout"
                    time.sleep(.5)
                    scan = client.post('/api/v1/account-scans/query', json={'scan_id': scan['scan_id']}).json()
                snapshot = client.post('/api/v1/accounts/query', json={}).json()
                accounts = snapshot["accounts"]
                assert len(accounts) == args.expected_accounts and snapshot["exit_code"] == 0, "accounts_incomplete"
                report["versions"] = sorted({i["version"] for a in accounts for i in a["instances"]})
                print(json.dumps(dict(stage="accounts_ready", count=len(accounts))), flush=True)

                def post(route, account_id, **values):
                    result = client.post("/api/v1/"+route, json=dict(account_id=account_id, **values))
                    assert result.headers.get("cache-control") == "no-store", "no_store_missing"
                    assert result.status_code in (200, 202), result.json().get("code", "api_failed")
                    return result.json()

                def wait(account_id, task):
                    deadline = time.monotonic()+210
                    while True:
                        assert time.monotonic() < deadline, "message_timeout"
                        result = client.post("/api/v1/message-read-tasks/query", json=dict(account_id=account_id, task_id=task["task_id"]))
                        if result.status_code == 503 and result.json().get("code") == "conversation_busy":
                            time.sleep(.2); continue
                        assert result.status_code == 200, "task_query_failed"
                        task = result.json()
                        if task["status"] not in ("queued", "running"):
                            assert task["status"] == "completed", task["error"] or "message_failed"
                            return task
                        time.sleep(.2)

                for index, account in enumerate(accounts):
                    aid = account["account_id"]
                    task = post("conversation-refreshes", aid)
                    while task["status"] in ("queued", "running"):
                        time.sleep(.2)
                        task = post("conversation-refreshes/query", aid, refresh_id=task["refresh_id"])
                    assert task["status"] == "completed", "conversation_failed"
                    conversations = post("conversations/query", aid, page_size=100)["items"]
                    chosen = []
                    for kind in ("contact", "group"):
                        sample = next((c for c in conversations if c["category"] == kind and not c["is_container"]), None)
                        if sample: chosen.append(sample)
                    if not chosen:
                        chosen = [c for c in conversations if not c["is_container"]][:1]
                    for conversation in chosen:
                        cid = conversation["conversation_id"]
                        begin = time.monotonic()
                        page = wait(aid, post("message-reads", aid, conversation_id=cid))["page"]
                        cold = time.monotonic()-begin
                        begin = time.monotonic()
                        warm = wait(aid, post("message-reads", aid, conversation_id=cid))["page"]
                        hot = time.monotonic()-begin
                        delta = wait(aid, post("message-reads", aid, conversation_id=cid, cursor=warm["newer_cursor"]))["page"]
                        assert not set(m["message_id"] for m in warm["items"]) & set(m["message_id"] for m in delta["items"]), "refresh_duplicates"
                        assert len({m["message_id"] for m in page["items"]}) == len(page["items"]), "duplicate_message_ids"
                        assert [m["sort_key"] for m in warm["items"]] == sorted(m["sort_key"] for m in warm["items"]), "wrong_order"
                        for msg in warm["items"]:
                            if msg["type"] != "system" and msg["sender"]["wxid"]:
                                assert msg["is_self"] == (msg["sender"]["wxid"] == account["wxid"]), "wrong_direction"
                            elif msg["is_self"] is not None:
                                raise AssertionError("unknown_sender_direction")
                            report["sample_counts"][msg["type"]] = report["sample_counts"].get(msg["type"], 0)+1
                            cases = ["self" if msg["is_self"] else "other" if msg["is_self"] is False else "neutral"]
                            if msg["type"] == "voice":
                                cases.append("voice_transcript" if msg["content"].get("transcript") else "voice_missing_transcript")
                            for case in cases:
                                report["coverage_counts"][case] = report["coverage_counts"].get(case, 0)+1
                        older_count = 0
                        if page["older_cursor"]:
                            older = wait(aid, post("message-reads", aid, conversation_id=cid, cursor=page["older_cursor"]))["page"]
                            older_count = len(older["items"])
                            assert not set(m["message_id"] for m in page["items"]) & set(m["message_id"] for m in older["items"]), "paging_duplicates"
                        image_results = []
                        for msg in [m for m in warm["items"] if m["type"] == "image"][:2]:
                            result = wait(aid, post("message-image-reads", aid, conversation_id=cid, message_id=msg["message_id"]))["image"]
                            if result["status"] == "available":
                                import base64
                                from wechat_messages.images import verified_image
                                assert verified_image(base64.b64decode(result["data_base64"])) == result["mime_type"], "invalid_image"
                            image_results.append(dict(status=result["status"], reason=result["reason"], quality=result["quality"]))
                            case = "image_" + (result["quality"] if result["status"] == "available" else result["reason"] or "unavailable")
                            report["coverage_counts"][case] = report["coverage_counts"].get(case, 0)+1
                        processes = {}
                        for worker in client.app.state.conversations.reader._workers.values():
                            process = psutil.Process(worker.process.pid)
                            for child in [process, *process.children(recursive=True)]:
                                processes[child.pid] = child
                                owned.append((child.pid, child.create_time()))
                        row = dict(account_index=index, category=conversation["category"], count=len(warm["items"]),
                            older_count=older_count, cold_seconds=round(cold, 3), warm_seconds=round(hot, 3),
                            status=warm["status"], issues=warm["issues"],
                            known_senders=sum(bool(m["sender"]["wxid"]) for m in warm["items"]),
                            voice_transcripts=sum(bool(m["content"].get("transcript")) for m in warm["items"]),
                            images=image_results,
                            worker_rss_mib=round(sum(p.memory_info().rss for p in processes.values())/1048576, 1),
                            worker_peak_rss_mib=round(sum(getattr(p.memory_info(), "peak_wset", p.memory_info().rss) for p in processes.values())/1048576, 1),
                            worker_cpu_seconds=round(sum(p.cpu_times().user+p.cpu_times().system for p in processes.values()), 3))
                        report["runs"].append(row)
                        print(json.dumps(dict(stage="conversation_verified", **row)), flush=True)
                report["completed"] = True
    except Exception as exc:
        report["error"] = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
    finally:
        report["observation"] = observer.result
        def survivors():
            remaining = []
            for pid, created in set(owned):
                try:
                    process = psutil.Process(pid)
                    if process.create_time() == created:
                        remaining.append(process)
                except psutil.NoSuchProcess:
                    pass
            return remaining
        # Job termination is asynchronous; observe bounded completion for all descendants.
        psutil.wait_procs(survivors(), timeout=3)
        report["workers_cleaned"] = not survivors()
        report["noninterference"] = "zero_wechat_focus_events" if observer.result.get("available") and not observer.result.get("wechat_foreground_events") and not observer.result.get("wechat_focus_events") else "inconclusive"
        report["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
        report["uncovered_types"] = [kind for kind in ("text", "image", "voice", "attachment", "system") if not report["sample_counts"].get(kind)]
        report["uncovered_cases"] = [case for case in ("self", "other", "neutral", "voice_transcript", "voice_missing_transcript", "image_original", "image_local", "image_thumbnail", "image_not_downloaded", "image_decryption_failed", "image_size_limit", "image_format_unsupported") if not report["coverage_counts"].get(case)]
        destination = output / ("live-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+".json")
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(dict(report=str(destination), completed=report["completed"], error=report.get("error"), workers_cleaned=report["workers_cleaned"], noninterference=report["noninterference"])), flush=True)
    return 0 if report["completed"] and report["workers_cleaned"] else 2


if __name__ == "__main__":
    sys.exit(main())
