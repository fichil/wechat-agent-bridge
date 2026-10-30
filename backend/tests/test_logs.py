import base64
from contextlib import closing
from datetime import datetime, timezone, timedelta
import io
import json
import sqlite3
import threading
import time
from unittest.mock import patch
import httpx
from PIL import Image
from backend.app.ai_provider import Provider
from backend.app.diagnostics import log_context, record_source, record_delta_groups, sanitize
from backend.app.log_storage import LOG_SCHEMA
from backend.app.log_models import LogQuery
from backend.app.storage import Store
from backend.app.conversations import ConversationError
from test_roles import RoleTestCase
import test_ai_execution as execution

class LogTests(RoleTestCase):
    configured=execution.ExecutionTests.configured
    ingest=execution.ExecutionTests.ingest
    start=execution.ExecutionTests.start
    wait_run=execution.ExecutionTests.wait_run

    def query(self, **fields):
        return self.post("logs/query",account_id=self.ids[0],**fields).json()

    def log(self, identity, account=0):
        return self.post("logs/get",account_id=self.ids[account],log_id=identity)

    def run_logs(self, rid):
        # Runs complete their business commit before the observer's final update.
        for _ in range(100):
            rows=[r for r in self.query()["items"] if r.get("task_id")==rid]
            if rows and all(r["application_status"] not in (None,"running","queued") for r in rows):return rows
            time.sleep(.005)
        return rows

    def test_wire_snapshot_names_reply_and_lazy_account_scoped_details(self):
        self.configured();self.ingest()
        run=self.wait_run(self.start()); rows=self.run_logs(run["run_id"])
        self.assertEqual(len(rows),1)
        item=rows[0];self.assertNotIn("details",item)
        self.assertEqual(item["profile_name"],self.profile["name"])
        self.assertEqual(item["application_status"],"completed")
        detail=self.log(item["log_id"]).json()["details"]
        self.assertEqual(detail["request_json"],self.provider.requests[-1])
        self.assertEqual(detail["input_snapshot"]["memory"],"")
        self.assertEqual(detail["parsed_result"]["reply"],run["reply"])
        self.assertEqual(self.log(item["log_id"],1).status_code,404)
        self.assertIn("test_text",[r["operation"] for r in self.query(scope="global")["items"]])
        self.store.call(lambda:(self.store.db.execute("UPDATE api_profiles SET name='后来改名' WHERE profile_id=?",(self.profile["profile_id"],)),self.store.db.commit()))
        self.assertEqual(self.log(item["log_id"]).json()["profile_name"],self.profile["name"])

    def test_all_protocol_wire_json_and_native_images_are_recorded_without_base64(self):
        profile=self.create_api()
        output=io.BytesIO();Image.new("RGB",(10,10),"red").save(output,"PNG")
        encoded=base64.b64encode(output.getvalue()).decode()
        envelope=dict(request_id="synthetic",instructions="json",character_card={},memory="before",messages=[dict(message_id="image-1",sent_at=None,is_self=False,sender={},type="image",content={"image":{"mime_type":"image/png","data_base64":encoded,"first_frame":True}})])
        for protocol in ("openai_responses","openai_chat_completions","anthropic_messages","gemini_generate_content"):
            configured={**profile,"protocol":protocol}
            with log_context(self.store,task_id="protocol-"+protocol):
                result=self.provider.generate(configured,"synthetic-secret",envelope,"schema")
            row=next(r for r in self.query()["items"] if r["task_id"]=="protocol-"+protocol)
            data=self.log(row["log_id"]).json()["details"]
            text=json.dumps(data)
            self.assertNotIn(encoded,text);self.assertIn("sha256",text)
            self.assertEqual(data["parsed_result"],result)
            self.assertEqual(data["input_snapshot"]["messages"][0]["content"]["image"]["data_base64"]["size_bytes"],len(output.getvalue()))
        with log_context(self.store,task_id="echo-image"):
            with self.assertRaises(ConversationError):
                Provider(httpx.MockTransport(lambda _:httpx.Response(400,text="bad image "+encoded))).generate(profile,"synthetic-secret",envelope,"schema")
        row=next(r for r in self.query()["items"] if r.get("task_id")=="echo-image")
        self.assertNotIn(encoded,json.dumps(self.log(row["log_id"]).json()))

    def test_http_invalid_json_refusal_truncation_and_timeout_are_redacted(self):
        profile=self.create_api();key="arbitrary-synthetic-secret-123"
        envelope=dict(request_id="errors",instructions="json",character_card={},memory="",messages=[])
        cases=[httpx.Response(401,json={"error":{"message":"echo "+key}}),httpx.Response(200,text="not json "+key),httpx.Response(200,json={"status":"incomplete","output":[]}),httpx.Response(200,json={"status":"completed","output":[{"type":"message","content":[{"type":"refusal","refusal":"no"}]}]}),httpx.Response(200,text="x"*(2*1024*1024+1))]
        for index,response in enumerate(cases):
            provider=Provider(httpx.MockTransport(lambda _:response))
            with log_context(self.store,task_id="error-"+str(index)):
                with self.assertRaises(ConversationError):provider.generate(profile,key,envelope,"schema")
            row=next(r for r in self.query()["items"] if r["task_id"]=="error-"+str(index))
            detail=self.log(row["log_id"]).json()
            self.assertEqual(row["status"],"failed");self.assertNotIn(key,json.dumps(detail))
            self.assertEqual(row["http_status"],response.status_code)
        def timeout(_):raise httpx.ReadTimeout("echo "+key)
        with log_context(self.store,task_id="timeout"):
            with self.assertRaises(ConversationError):Provider(httpx.MockTransport(timeout)).generate(profile,key,envelope,"schema")
        row=next(r for r in self.query()["items"] if r["task_id"]=="timeout")
        self.assertEqual(row["error"],"ai_network_error")
        content=self.store.call(lambda:"".join(r[0] for r in self.store.db.execute("SELECT payload FROM diagnostic_details")))
        self.assertNotIn(key,content)

    def test_native_format_fallback_and_image_probe_have_independent_attempts(self):
        from backend.tests.ai_fakes import FakeProvider
        fake=FakeProvider()
        def respond(request):
            body=json.loads(request.content)
            if body.get("text",{}).get("format",{}).get("type")=="json_schema":
                return httpx.Response(400,json={"error":"unsupported text.format json_schema"})
            return fake.respond(request)
        profile=self.create_api()
        with log_context(self.store,task_id="fallback"):
            result=Provider(httpx.MockTransport(respond)).test(profile,"synthetic-test-key","json")
        rows=[r for r in self.query()["items"] if r["task_id"]=="fallback"]
        self.assertEqual(len(rows),3)
        self.assertEqual(result["output_mode"],"json")
        self.assertEqual(sorted(r["operation"] for r in rows),["test_image","test_text","test_text"])

    def test_source_reads_merge_only_automatic_identical_results_and_queries_do_not_log(self):
        args=dict(aid=self.ids[0],cid=self.cids[0],automatic=True)
        for _ in range(3):record_source(self.store,"changes",{"cursor":1},{"unchanged":True},**args)
        data=self.query(category="source",scope="account")["items"]
        self.assertEqual(len(data),1);self.assertEqual(data[0]["count"],3)
        for _ in range(2):record_source(self.store,"messages",{},dict(items=[{"text":"new"}]),aid=self.ids[0],cid=self.cids[0])
        for _ in range(2):record_source(self.store,"changes",{},None,error="offline",**args)
        records=self.query(category="source",scope="account")["items"]
        self.assertEqual(len(records),5)
        self.log(records[0]["log_id"]);self.query(category="source")
        self.assertEqual(len(self.query(category="source",scope="account")["items"]),5)

    def test_pagination_scope_clear_and_waiting_messages(self):
        self.configured();self.ingest(1,type="attachment",content={"filename":"test.txt"});self.ingest(2,type="voice",content={})
        for aid in self.ids:
            for n in range(4):self.store.log_write(dict(category="source",operation="read",account_id=aid),dict(number=n))
        first=self.query(category="source",scope="account",page_size=2)
        second=self.query(category="source",scope="account",page_size=2,cursor=first["next_cursor"])
        self.assertFalse(set(x["log_id"] for x in first["items"])&set(x["log_id"] for x in second["items"]))
        mismatch=self.post("logs/query",account_id=self.ids[1],category="source",scope="account",cursor=first["next_cursor"])
        self.assertNotEqual(mismatch.status_code,200)
        pending=self.post("ai-runs/messages/query",account_id=self.ids[0],conversation_id=self.cids[0]).json()
        self.assertEqual({m["ai_state"]["status"] for m in pending["items"]},{"unsupported","waiting_transcript"})
        self.assertEqual(self.post("ai-runs/messages/query",account_id=self.ids[1],conversation_id=self.cids[0]).status_code,404)
        active=self.store.log_write(dict(account_id=self.ids[0],category="source",operation="running",status="running"),{})
        self.post("logs/clear",account_id=self.ids[0],scope="account")
        self.assertEqual(self.log(active).status_code,200)
        self.assertTrue(self.store.logs(LogQuery(account_id=self.ids[1],category="source",scope="account"))["items"])
        self.assertEqual(self.store.messages(self.ids[0],self.cids[0])["items"].__len__(),2)

    def test_time_filters_source_hooks_and_usage(self):
        result=self.app.state.bridge.discovery.scan()
        self.assertFalse(result.get("error"))
        self.assertTrue(self.query(category="source",scope="account")["items"])
        since=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()
        until=(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat()
        self.assertTrue(self.query(category="source",since=since,until=until)["items"])
        self.assertFalse(self.query(category="source",since=until)["items"])
        self.assertEqual(self.post("logs/query",account_id=self.ids[0],since=until,until=since).status_code,422)
        record_delta_groups(self.store,self.ids[0],[],dict(groups=[dict(source_id="synthetic_0",items=[{"text":"new"}])]),.01)
        rows=self.query(category="source",conversation_id=self.cids[0])["items"]
        self.assertEqual(rows[0]["operation"],"changes")
        profile=self.create_api()
        output=dict(status="completed",id="provider-response-id",usage={"input_tokens":12},output=[dict(type="message",content=[dict(type="output_text",text='{"memory":"ok","reply":""}')])])
        with log_context(self.store,task_id="usage"):
            Provider(httpx.MockTransport(lambda _:httpx.Response(200,json=output))).generate(profile,"synthetic-key",dict(request_id="usage",instructions="json",character_card={},memory="",messages=[]),"schema")
        row=next(r for r in self.query()["items"] if r.get("task_id")=="usage")
        self.assertEqual(row["provider_request_id"],"provider-response-id")
        self.assertEqual(self.log(row["log_id"]).json()["details"]["usage"],{"input_tokens":12})

    def test_retention_capacity_and_active_protection(self):
        old=self.store.log_write(dict(category="source",operation="old",account_id=self.ids[0]),{"text":"old"})
        age=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat()
        self.store.call(lambda:self.store.db.execute("UPDATE diagnostic_logs SET updated_at=? WHERE log_id=?",(age,old)))
        self.query();self.assertEqual(self.log(old).status_code,404)
        self.store.log_limit=120
        active=self.store.log_write(dict(category="source",operation="active",status="running"),{"text":"x"*100})
        finished=self.store.log_write(dict(category="source",operation="finished"),{"text":"y"*100})
        self.assertEqual(self.log(active).status_code,200);self.assertEqual(self.log(finished).status_code,404)
        self.assertIn("容量",self.query()["warning"])

    def test_logging_failure_cannot_fail_committed_memory_or_reinvoke_provider(self):
        self.configured();self.ingest()
        before=len(self.provider.requests)
        with patch.object(self.store,"_log_put",side_effect=sqlite3.OperationalError("synthetic disk error")):
            run=self.wait_run(self.start())
        self.assertEqual(run["status"],"completed")
        self.assertEqual(self.preferences()["memory_revision"],1)
        self.assertEqual(len(self.provider.requests),before+1)
        self.assertIn("日志记录异常",self.query()["warning"])

    def test_secret_fields_and_notifications_never_contain_private_bodies(self):
        events=[]
        self.store.notify=events.append
        identity=self.store.log_write(dict(category="source",operation="read",account_id=self.ids[0]),dict(
            Authorization="Bearer synthetic-header",cookie="synthetic-cookie",db_key="synthetic-decryption",
            result=dict(text="用户要求保留的聊天正文",data_base64=base64.b64encode(b"synthetic binary").decode(),mime_type="image/png")))
        text=json.dumps(self.log(identity).json())
        self.assertNotIn("synthetic-header",text);self.assertNotIn("synthetic-cookie",text);self.assertNotIn("synthetic-decryption",text)
        self.assertIn("sha256",text)
        self.assertNotIn("聊天正文",json.dumps(events,ensure_ascii=False))
        self.assertEqual(events[0]["resources"],["logs"])

    def test_memory_conflict_and_cancel_are_separate_from_provider_success(self):
        self.configured();self.ingest()
        original=self.provider.generate
        for disabled in (False,True):
            entered,release=threading.Event(),threading.Event()
            def block(*args):
                result=original(*args);entered.set();release.wait(4);return result
            with patch.object(self.provider,"generate",side_effect=block):
                run=self.start();self.assertTrue(entered.wait(3))
                pref=self.preferences()
                self.update(pref["revision"],**({"ai_enabled":False} if disabled else {"memory":"manual"}))
                release.set();result=self.wait_run(run)
            row=self.run_logs(run["run_id"])[0]
            self.assertEqual(row["status"],"completed")
            self.assertEqual(row["application_status"],"cancelled" if disabled else "conflicted")

    def test_v5_migration_rollback_legacy_and_restart_interruption(self):
        self.configured();self.ingest();run=self.wait_run(self.start());self.run_logs(run["run_id"])
        self.store.close()
        with closing(sqlite3.connect(self.path)) as db:
            from migration_helpers import remove_v7
            remove_v7(db)
            db.executescript("DROP TABLE diagnostic_details; DROP TABLE diagnostic_logs; DELETE FROM schema_migrations WHERE version=6; PRAGMA user_version=5;")
        with patch("backend.app.storage.LOG_SCHEMA",(*LOG_SCHEMA,"INVALID SQL")):
            with self.assertRaises(ConversationError):Store(self.path)
        with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],5)
        with closing(Store(self.path)) as store:
            legacy=store.log_detail(self.ids[0],"legacy:"+run["run_id"])
            self.assertTrue(legacy["legacy"]);self.assertIn("未记录",legacy["details"]["note"])
            identity=store.log_write(dict(category="source",operation="crash",status="running"),{})
            store.log_write(dict(log_id=legacy["log_id"],application_status="running"))
        with closing(Store(self.path)) as store:
            self.assertEqual(store.log_detail(self.ids[0],identity)["status"],"interrupted")
            summaries=store.logs(LogQuery(account_id=self.ids[0]))["items"]
            self.assertEqual(next(r for r in summaries if r["log_id"]==legacy["log_id"])["application_status"],"completed")
            self.assertEqual(store.preferences(self.ids[0],self.cids[0])["memory_revision"],1)
