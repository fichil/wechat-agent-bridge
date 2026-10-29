"""Explicit read-only live archive acceptance; reports contain only aggregate evidence."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",action="store_true")
    args=parser.parse_args()
    if not args.run:
        parser.print_help();return 0
    import psutil
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    from backend.app.storage import DEFAULT_PATH,now
    from wechat_account_probe.native import enumerate_instances
    from wechat_account_probe.observation import Observation
    psutil.Process().nice(psutil.IDLE_PRIORITY_CLASS)
    report=dict(completed=False,accounts=0,samples=[],versions=[],offline_verified=False)
    observer=Observation([i.pid for i in enumerate_instances()[0]])
    checked=[]
    def post(client,route,body):
        response=client.post("/api/v1/"+route,json=body)
        if response.status_code not in (200,202):
            raise AssertionError(response.json().get("code","request_failed"))
        return response.json()
    def wait(client,route,body):
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            task=post(client,route,body)
            if task["status"] not in ("queued","running"):
                assert task["status"]=="completed",task.get("error") or "task_failed"
                return task
            time.sleep(.2)
        raise AssertionError("task_timeout")
    try:
        with observer:
            with TestClient(create_app(database_path=DEFAULT_PATH,monitor=False),base_url="http://127.0.0.1:8765") as client:
                scan=post(client,"account-scans",{})
                wait(client,"account-scans/query",dict(scan_id=scan["scan_id"]))
                accounts=post(client,"accounts/query",{})["accounts"]
                accounts=[a for a in accounts if a.get("account_id") and a["source_state"]=="online"]
                report["accounts"]=len(accounts)
                report["versions"]=sorted({i["version"] for a in accounts for i in a["instances"]})
                print(json.dumps(dict(stage="accounts",count=len(accounts))),flush=True)
                assert accounts,"no_online_accounts"
                for index,account in enumerate(accounts):
                    aid=account["account_id"]
                    refresh=post(client,"conversation-refreshes",dict(account_id=aid))
                    wait(client,"conversation-refreshes/query",dict(account_id=aid,refresh_id=refresh["refresh_id"]))
                    page=post(client,"conversations/query",dict(account_id=aid,page_size=100))
                    conv=next((c for c in page["items"] if not c["is_container"] and c["category"] in ("contact","group")),None)
                    if conv is None:continue
                    body=dict(account_id=aid,conversation_id=conv["conversation_id"],page_size=50)
                    start=time.perf_counter();task=post(client,"message-reads",body)
                    final=wait(client,"message-read-tasks/query",dict(account_id=aid,task_id=task["task_id"]))
                    cold=time.perf_counter()-start
                    samples=[]
                    for _ in range(20):
                        start=time.perf_counter();cached=post(client,"messages/query",body)
                        samples.append((time.perf_counter()-start)*1000)
                    assert cached["items"]==final["page"]["items"],"archive_readback_differs"
                    checked.append((body,[m["message_id"] for m in cached["items"]]))
                    # One bounded baseline shard per account; remaining shards continue in the running service.
                    source=client.app.state.bridge.discovery.online(aid)
                    start=time.perf_counter()
                    delta=client.app.state.bridge.reader.changes(source,client.app.state.store.states(aid))
                    client.app.state.store.save_delta(aid,delta)
                    row=dict(account_index=index,message_count=len(cached["items"]),cold_seconds=round(cold,3),
                             cached_p95_ms=round(sorted(samples)[18],2),baseline_groups=len(delta["groups"]),
                             baseline_seconds=round(time.perf_counter()-start,3),issues=delta.get("issues",[]))
                    processes=[]
                    for worker in client.app.state.bridge.reader._workers.values():
                        process=psutil.Process(worker.process.pid)
                        processes.extend([process,*process.children(recursive=True)])
                    row["worker_rss_mib"]=round(sum(p.memory_info().rss for p in processes)/1048576,1)
                    row["worker_cpu_seconds"]=round(sum(p.cpu_times().user+p.cpu_times().system for p in processes),2)
                    row["worker_read_bytes"]=sum(p.io_counters().read_bytes for p in processes)
                    report["samples"].append(row)
                    print(json.dumps(dict(stage="archive",**row)),flush=True)
            reader=Mock();probe=Mock(side_effect=AssertionError("offline_source_access"))
            with TestClient(create_app(probe,conversation_reader=reader,database_path=DEFAULT_PATH,monitor=False),base_url="http://127.0.0.1:8765") as client:
                for body,ids in checked:
                    cached=post(client,"messages/query",body)
                    assert [m["message_id"] for m in cached["items"]]==ids,"restart_identity_changed"
                probe.assert_not_called();reader.messages.assert_not_called();reader.validate.assert_not_called()
                report["offline_verified"]=bool(checked)
            report["completed"]=bool(checked)
    except Exception as exc:
        report["error"]=str(exc) if isinstance(exc,AssertionError) else type(exc).__name__
    finally:
        report["observation"]=observer.result
        report["noninterference"]="zero_wechat_focus_events" if observer.result.get("available") and not observer.result.get("wechat_foreground_events") and not observer.result.get("wechat_focus_events") else "inconclusive"
        report["timestamp"]=now()
        output=ROOT/".local/archive-acceptance";output.mkdir(parents=True,exist_ok=True)
        (output/"live.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if report["completed"] else 2


if __name__=="__main__":
    sys.exit(main())
