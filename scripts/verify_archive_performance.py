"""Synthetic 100,000-message archive benchmark. Never accesses WeChat."""
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    from backend.app.storage import Store, now
    from backend.app.services import browser_values
    from wechat_account_probe.models import aggregate, instance_result, Instance
    output=ROOT/".local/archive-acceptance"
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as temp:
        path=Path(temp)/"benchmark.sqlite3"
        store=Store(path)
        snapshot=store.save_accounts(browser_values(aggregate([instance_result(Instance(1,134000000000000001,1,"4.1.15.56"),wxid="synthetic",nickname="合成",status="complete")])))
        aid=snapshot["accounts"][0]["account_id"]
        store.save_conversations(aid,dict(items=[dict(source_id="benchmark",name="合成性能测试",category="contact",is_container=False,summary=None,last_message_at=None,unread_count=0)],status="complete",issues=[]))
        cid=store.conversations(aid)["items"][0]["conversation_id"]
        started=time.perf_counter()
        for offset in range(0,100000,100):
            items=[dict(_key=[i,1780000000,"message/message_0.db",i],_ref=dict(shard="message/message_0.db",local_id=i,server_id=str(9007199254740993+i)),type="text",sent_at="2026-10-28T00:00:00Z",content=dict(text="合成数据，用于索引和分页性能验证。"),sender=dict(nickname="合成发送者",wechat_id=None,wxid="synthetic",remark=None,status="complete"),is_self=True,issues=[]) for i in range(offset+1,offset+101)]
            store.save_history(aid,cid,dict(items=items,has_more=False,status="complete",issues=[]),dict(boundary=None))
        load_seconds=time.perf_counter()-started
        def deep():
            row=store.db.execute("SELECT sort_key,message_id,source_key FROM messages WHERE local_id=50000").fetchone()
            with store.db:
                cursor=store._cursor(aid,cid,"messages",dict(boundary=[row[0],row[1]],source_boundary=json.loads(row[2])))
            plan=[r[3] for r in store.db.execute("EXPLAIN QUERY PLAN SELECT payload FROM messages WHERE account_id=? AND conversation_id=? AND (sort_key,message_id)<(?,?) ORDER BY sort_key DESC,message_id DESC LIMIT 51",(aid,cid,row[0],row[1]))]
            return cursor,plan
        cursor,plan=store.call(deep)
        store.close()
        probe=Mock(side_effect=AssertionError("benchmark must not scan"))
        reader=Mock()
        timings={}
        with TestClient(create_app(probe,conversation_reader=reader,database_path=path,monitor=False),base_url="http://127.0.0.1:8765") as client:
            for name,boundary in (("latest",None),("deep",cursor)):
                samples=[]
                for i in range(55):
                    start=time.perf_counter()
                    response=client.post("/api/v1/messages/query",json=dict(account_id=aid,conversation_id=cid,cursor=boundary,page_size=50))
                    assert response.status_code==200 and len(response.json()["items"])==50
                    if i>=5: samples.append((time.perf_counter()-start)*1000)
                timings[name]=dict(p50_ms=round(statistics.median(samples),2),p95_ms=round(sorted(samples)[47],2))
            probe.assert_not_called();reader.read.assert_not_called();reader.validate.assert_not_called();reader.messages.assert_not_called()
        report=dict(timestamp=now(),rows=100000,ingest_seconds=round(load_seconds,2),latency=timings,query_plan=plan,source_calls=0,
                    passed=all(v["p95_ms"]<200 for v in timings.values()) and any("message_order" in s for s in plan))
        (output/"performance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(report,ensure_ascii=False))
        return 0 if report["passed"] else 1


if __name__=="__main__":
    sys.exit(main())
