"""Explicit sticker acceptance using a private archive copy; never operate WeChat UI."""
import argparse
import base64
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from urllib.request import build_opener, ProxyHandler, Request
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--sample-time", default="2026-10-28T05:30:00+00:00", help="UTC timestamp near the requested examples")
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return 0
    import psutil
    from PIL import Image
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    from backend.app.storage import DEFAULT_PATH, now
    from wechat_account_probe.native import enumerate_instances
    from wechat_account_probe.observation import Observation
    psutil.Process().nice(psutil.IDLE_PRIORITY_CLASS)
    output = ROOT / ".local" / "sticker-acceptance"
    output.mkdir(parents=True, exist_ok=True)
    report = dict(completed=False, samples=[], offline_verified=False, bindings="live_service_then_worker_revalidation")
    observer = Observation([i.pid for i in enumerate_instances()[0]])

    def post(client, route, body):
        response = client.post("/api/v1/"+route, json=body)
        assert response.status_code in (200, 202), response.json().get("code", "request_failed")
        assert response.headers["content-type"] == "application/json", "json_required"
        return response.json()

    def wait(client, route, body):
        deadline = time.monotonic()+200
        while time.monotonic() < deadline:
            task = post(client, route, body)
            if task["status"] not in ("queued", "running"):
                return task
            time.sleep(.1)
        raise AssertionError("task_timeout")

    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(Request("http://127.0.0.1:8765/api/v1/accounts/query", data=b"{}", headers={"Content-Type": "application/json"}), timeout=5) as response:
            snapshot = json.load(response)
        snapshot["accounts"] = [a for a in snapshot["accounts"] if a["source_state"] == "online" and a["instances"]]
        assert snapshot["accounts"], "no_online_accounts"
        report["versions"] = sorted({i["version"] for a in snapshot["accounts"] for i in a["instances"]})
        with tempfile.TemporaryDirectory(prefix="isolated-", dir=output) as temporary, observer:
            assert Path(temporary).resolve().is_relative_to(output.resolve())
            path = Path(temporary)/"archive.sqlite3"
            source = sqlite3.connect(DEFAULT_PATH.resolve().as_uri()+"?mode=ro", uri=True, timeout=2)
            dest = sqlite3.connect(path)
            try:
                source.backup(dest, pages=128)
                rows = dest.execute("SELECT account_id,conversation_id,message_id FROM messages WHERE json_extract(payload,'$.type')='sticker' ORDER BY abs(strftime('%s',json_extract(payload,'$.sent_at'))-strftime('%s',?)) LIMIT 12", (args.sample_time,)).fetchall()
            finally:
                source.close()
                dest.close()
            checked, static, animated = [], False, False
            with TestClient(create_app(lambda: snapshot, database_path=path, monitor=False), base_url="http://127.0.0.1:8765") as client:
                scan = post(client, "account-scans", {})
                scan = wait(client, "account-scans/query", dict(scan_id=scan["scan_id"]))
                assert scan["status"] == "completed", "binding_import_failed"
                print(json.dumps(dict(stage="bindings_ready", accounts=len(snapshot["accounts"]), candidates=len(rows))), flush=True)
                for index, (aid, cid, mid) in enumerate(rows):
                    body = dict(account_id=aid, conversation_id=cid, message_id=mid)
                    start = time.monotonic()
                    task = post(client, "message-image-reads", body)
                    task = wait(client, "message-read-tasks/query", dict(account_id=aid, task_id=task["task_id"]))
                    result = task.get("image") or dict(status="unavailable", reason=task.get("error"))
                    sample = dict(index=index, status=result["status"], reason=result.get("reason"), seconds=round(time.monotonic()-start, 3))
                    if result["status"] == "available":
                        data = base64.b64decode(result["data_base64"], validate=True)
                        with Image.open(io.BytesIO(data)) as image:
                            frames = getattr(image, "n_frames", 1)
                            duration = 0
                            for frame in range(frames):
                                image.seek(frame)
                                duration += image.info.get("duration", 0)
                            sample.update(mime=result["mime_type"], quality=result["quality"], bytes=len(data), frames=frames,
                                          duration_ms=duration, sha256=hashlib.sha256(data).hexdigest())
                        cached = post(client, "message-images/query", body)
                        assert cached["data_base64"] == result["data_base64"], "cache_changed_bytes"
                        checked.append((body, cached["data_base64"]))
                        static |= frames == 1 and result["quality"] == "original"
                        animated |= frames > 1 and result["mime_type"] == "image/gif" and result["quality"] == "original"
                    report["samples"].append(sample)
                    print(json.dumps(dict(stage="sample", **sample)), flush=True)
                    if static and animated:
                        break
                processes = []
                for worker in client.app.state.bridge.reader._workers.values():
                    if worker.process.poll() is None:
                        process = psutil.Process(worker.process.pid)
                        processes.extend([process, *process.children(recursive=True)])
                processes = list({p.pid: p for p in processes}.values())
                report["worker_rss_mib"] = round(sum(p.memory_info().rss for p in processes)/1048576, 1)
                report["worker_cpu_seconds"] = round(sum(p.cpu_times().user+p.cpu_times().system for p in processes), 2)
                report["worker_read_bytes"] = sum(p.io_counters().read_bytes for p in processes)
                report["workers_idle_priority"] = all(p.nice() == psutil.IDLE_PRIORITY_CLASS for p in processes)
            reader = Mock()
            probe = Mock(side_effect=AssertionError("offline_source_access"))
            with TestClient(create_app(probe, conversation_reader=reader, database_path=path, monitor=False), base_url="http://127.0.0.1:8765") as client:
                for body, encoded in checked:
                    result = post(client, "message-images/query", body)
                    assert result["data_base64"] == encoded, "restart_changed_bytes"
                    task = post(client, "message-image-reads", body)
                    task = wait(client, "message-read-tasks/query", dict(account_id=body["account_id"], task_id=task["task_id"]))
                    assert task["image"]["data_base64"] == encoded, "offline_cache_failed"
                reader.message_image.assert_not_called()
                probe.assert_not_called()
                report["offline_verified"] = bool(checked)
            report["completed"] = static and animated and report["offline_verified"]
    except Exception as exc:
        report["error"] = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
    finally:
        report["observation"] = observer.result
        report["noninterference"] = "zero_wechat_focus_events" if observer.result.get("available") and not observer.result.get("wechat_foreground_events") and not observer.result.get("wechat_focus_events") else "inconclusive"
        report["timestamp"] = now()
        path = output / ("live-"+datetime.now().strftime("%Y%m%d-%H%M%S")+".json")
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(dict(stage="complete", **report), ensure_ascii=False), flush=True)
    return 0 if report["completed"] else 2


if __name__ == "__main__":
    sys.exit(main())
