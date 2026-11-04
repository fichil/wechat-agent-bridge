"""Disposable 28k / 505 MiB cleanup acceptance; never opens production data."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/tests")]
from test_roles import RoleTestCase
from backend.app.log_storage import pack, stamp


def verify():
    case = RoleTestCase()
    case.setUp()
    try:
        store = case.store
        rows, body = 28000, pack({"text": "synthetic diagnostic " * 900})
        size, now = len(body.encode()), stamp()
        def seed():
            with store.db:
                store.db.execute("DELETE FROM diagnostic_logs")
                store.db.executemany("INSERT INTO diagnostic_logs VALUES(?,?,NULL,'source',NULL,?,?,'completed',0,NULL,?)",
                    ((str(i), case.ids[0], now, now, pack(dict(log_id=str(i), account_id=case.ids[0], category="source", operation="synthetic", status="completed", started_at=now, updated_at=now, count=1))) for i in range(rows)))
                store.db.executemany("INSERT INTO diagnostic_details VALUES(?,?,?)", ((str(i), body, size) for i in range(rows)))
        store.call(seed)
        first = threading.Event()
        batches, timings = [], []
        original = store.log_clear_batch
        def measured(task, targets):
            started = time.perf_counter()
            value = original(task, targets)
            batches.append(dict(rows=len(targets), bytes=sum(r[2] for r in targets), ms=round((time.perf_counter()-started)*1000, 3)))
            first.set()
            return value
        store.log_clear_batch = measured
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=1) as pool:
            clear = pool.submit(case.app.state.log_clear.clear_and_wait, case.ids[0], "account")
            assert first.wait(10)
            completed_during_cleanup = 0
            while not clear.done():
                query_started = time.perf_counter()
                assert len(store.accounts()) == 2
                progress = store.log_clear_task(case.ids[0], "account")
                timings.append((time.perf_counter()-query_started)*1000)
                if progress["status"] in ("queued", "running"):
                    completed_during_cleanup += 1
                time.sleep(.005)
            assert clear.result()["deleted_count"] == rows
        duration = (time.perf_counter()-started)*1000
        assert completed_during_cleanup > 0
        assert max(timings) < 8000, timings
        assert all(b["rows"] <= 200 and b["bytes"] <= 4*1024*1024 for b in batches)
        assert store.call(lambda: store.db.execute("SELECT count(*) FROM diagnostic_logs").fetchone()[0]) == 0
        assert store.call(lambda: store.db.execute("SELECT count(*) FROM diagnostic_details").fetchone()[0]) == 0
        return dict(rows=rows, detail_bytes=rows*size, deleted_count=rows,
            cleanup_ms=round(duration, 3), batches=len(batches), max_batch_ms=max(b["ms"] for b in batches),
            max_batch_rows=max(b["rows"] for b in batches), max_batch_bytes=max(b["bytes"] for b in batches),
            concurrent_queries=completed_during_cleanup, max_query_ms=round(max(timings), 3),
            schema_version=store.call(lambda: store.db.execute("PRAGMA user_version").fetchone()[0]))
    finally:
        case.tearDown()


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
