"""Disposable 13k / 240 MiB diagnostic archive acceptance; never opens live data."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.storage import Store
from backend.app.log_storage import pack, stamp


def verify():
    with tempfile.TemporaryDirectory(prefix='bridge-log-performance-') as folder:
        store=Store(Path(folder)/'synthetic.sqlite3')
        try:
            rows=13000;body=pack({'text':'合成日志数据 '*2000})
            size=len(body.encode());now=stamp()
            def seed():
                with store.db:
                    for index in range(rows):
                        identity=str(index)
                        value=dict(log_id=identity,category='source',operation='synthetic',status='completed',updated_at=now,count=1)
                        store.db.execute('INSERT INTO diagnostic_logs VALUES(?,NULL,NULL,\'source\',NULL,?,?,\'completed\',?,NULL,?)',
                                         (identity,now,now,int(index==0),pack(value)))
                        store.db.execute('INSERT INTO diagnostic_details VALUES(?,?,?)',(identity,body,size))
            store.call(seed)
            assert rows*size>=240*1024*1024
            def measure():
                plans=[r[3] for r in store.db.execute('EXPLAIN QUERY PLAN SELECT SUM(size_bytes) FROM diagnostic_details INDEXED BY logs_detail_sizes')]
                assert any('COVERING INDEX logs_detail_sizes' in p for p in plans),plans
                timings=[]
                for _ in range(20):
                    start=time.perf_counter();assert store._log_size()==rows*size
                    timings.append((time.perf_counter()-start)*1000)
                return plans,timings
            plans,timings=store.call(measure)
            store.log_limit=rows*size-450*size
            batches=[]
            for _ in range(3):
                def prune():
                    start=time.perf_counter()
                    with store.db: store._log_prune()
                    count=store.db.execute('SELECT count(*) FROM diagnostic_logs').fetchone()[0]
                    assert store.db.execute("SELECT 1 FROM diagnostic_logs WHERE log_id='0'").fetchone()
                    return dict(remaining=count,elapsed_ms=round((time.perf_counter()-start)*1000,3))
                batches.append(store.call(prune))
            assert [b['remaining'] for b in batches]==[12800,12600,12550],batches
            assert store.call(lambda:store.db.execute('PRAGMA user_version').fetchone()[0])==12
            return dict(rows=rows,detail_bytes=rows*size,query_plan=plans,
                        capacity_ms=dict(min=round(min(timings),3),max=round(max(timings),3)),cleanup_batches=batches,schema_version=12)
        finally: store.close()


if __name__=='__main__': print(json.dumps(verify(),ensure_ascii=False,indent=2))
