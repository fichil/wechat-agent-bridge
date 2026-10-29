import base64
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.storage import Store, order_key
from backend.app.conversations import ConversationError
from backend.app.conversation_settings import ConversationSettingsStore, SettingsError
from backend.app.conversation_models import Exclusions
from backend.app.runtime import Bridge, WorkQueue
from backend.app.services import browser_values
from test_conversations import accounts, Reader, rows
from test_message_api import message


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/"archive.sqlite3"
        self.store = Store(self.path)
        snapshot = self.store.save_accounts(browser_values(accounts()))
        self.aid = snapshot["accounts"][0]["account_id"]
        self.store.save_conversations(self.aid, dict(items=rows(1), status="complete", issues=[]))
        self.cid = self.store.conversations(self.aid)["items"][0]["conversation_id"]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def ingest(self, numbers, more=False, boundary=None, issues=None):
        result = dict(items=[message(i) for i in numbers], has_more=more, issues=issues or [], status="partial" if issues else "complete", retry_required=bool(issues))
        self.store.save_history(self.aid, self.cid, result, dict(boundary=boundary))

    def test_restart_preserves_ids_messages_cursor_and_image_offline(self):
        self.ingest(range(1, 101))
        page = self.store.messages(self.aid,self.cid,size=20)
        mid = page["items"][-1]["message_id"]
        self.store.image(self.aid,self.cid,mid,dict(status="available",mime_type="image/png",data_base64=base64.b64encode(b"synthetic").decode(),quality="thumbnail"))
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.account(self.aid)["source_state"], "unknown")
        self.assertEqual(self.store.messages(self.aid,self.cid,size=20)["items"],page["items"])
        older = self.store.messages(self.aid,self.cid,page["older_cursor"],20)
        self.assertEqual(len(older["items"]),20)
        self.assertFalse({m["message_id"] for m in page["items"]}&{m["message_id"] for m in older["items"]})
        self.assertEqual(self.store.image(self.aid,self.cid,mid)["status"],"available")

    def test_dedup_and_changed_content_keep_message_identity(self):
        self.ingest([1,2])
        before = self.store.messages(self.aid,self.cid)["items"]
        self.ingest([1,2])
        changed = message(2); changed["content"]["text"]="changed"
        self.store.save_history(self.aid,self.cid,dict(items=[changed],has_more=True,issues=[],status="complete"),dict(boundary=None))
        after = self.store.messages(self.aid,self.cid)["items"]
        self.assertEqual([r["message_id"] for r in before],[r["message_id"] for r in after])
        self.assertEqual(after[-1]["content"]["text"],"changed")

    def test_sticker_restart_preserves_gif_and_shared_image_quota(self):
        from test_stickers import picture
        item = message(); item.update(type="sticker",content=dict(description="表情"))
        self.store.save_history(self.aid,self.cid,dict(items=[item],has_more=False,issues=[],status="complete"),dict(boundary=None))
        mid = self.store.messages(self.aid,self.cid)["items"][0]["message_id"]
        data = picture()
        value = dict(status="available",mime_type="image/gif",data_base64=base64.b64encode(data).decode(),quality="original")
        self.store.image(self.aid,self.cid,mid,value)
        self.store.close(); self.store = Store(self.path)
        self.assertEqual(base64.b64decode(self.store.image(self.aid,self.cid,mid)["data_base64"]),data)
        self.assertEqual(self.store.messages(self.aid,self.cid)["items"][0]["type"],"sticker")
        self.store.image_limit = len(data)
        self.ingest([2])
        other = self.store.messages(self.aid,self.cid)["items"][-1]["message_id"]
        self.store.image(self.aid,self.cid,other,value)
        self.assertTrue(self.store.image(self.aid,self.cid,mid)["needs_sync"])
        self.assertEqual(self.store.image(self.aid,self.cid,other)["status"],"available")

    def test_change_feed_drains_batches_low_sort_arrivals_and_edits(self):
        self.ingest([500])
        initial = self.store.messages(self.aid,self.cid)
        cursor = initial["newer_cursor"]
        self.ingest(range(1,122))
        changed = message(500); changed["content"]["text"] = "edited"
        self.store.save_history(self.aid,self.cid,dict(items=[changed],has_more=False,issues=[],status="complete"),dict(boundary=None))
        seen=[]
        for _ in range(10):
            page = self.store.messages(self.aid,self.cid,cursor,25)
            seen.extend(page["items"]); cursor=page["newer_cursor"]
            if not page["has_more"]: break
        self.assertEqual(len(seen),122)
        self.assertEqual(len({m["message_id"] for m in seen}),122)
        self.assertIn("edited",[m["content"]["text"] for m in seen])
        self.assertEqual(self.store.messages(self.aid,self.cid,cursor)["items"],[])

    def test_byte_bounded_pages_do_not_skip_messages_or_request_source(self):
        self.ingest(range(1,21))
        with patch("backend.app.storage.PAGE_BYTES",1024):
            cursor=None;seen=[]
            for _ in range(25):
                page=self.store.messages(self.aid,self.cid,cursor,20)
                self.assertFalse(page["needs_sync"])
                seen.extend(m["message_id"] for m in page["items"])
                cursor=page["older_cursor"]
                if not cursor:break
            self.assertEqual(len(seen),20)
            self.assertEqual(len(set(seen)),20)

    def test_failed_schema_migration_rolls_back_and_future_version_is_preserved(self):
        from backend.app.storage import SCHEMA
        path=Path(self.temp.name)/"failed-migration.sqlite3"
        with patch("backend.app.storage.SCHEMA",SCHEMA+"\nINVALID SQL;"):
            with self.assertRaises(ConversationError): Store(path)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0],0)
            db.execute("PRAGMA user_version=99")
        with self.assertRaises(ConversationError): Store(path)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],99)

    def test_index_migration_failure_preserves_existing_archive_and_is_retryable(self):
        self.ingest([1,2]); self.store.close()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("DROP INDEX message_pending_transcripts")
            db.execute("DELETE FROM schema_migrations WHERE version=2")
            db.execute("DROP TABLE conversation_preferences")
            db.execute("DROP TABLE role_cards")
            db.execute("DROP INDEX conversation_owner")
            db.execute("DELETE FROM schema_migrations WHERE version=3")
            db.execute("PRAGMA user_version=1")
            db.execute("CREATE TRIGGER fail_migration BEFORE INSERT ON schema_migrations WHEN new.version=2 BEGIN SELECT RAISE(ABORT,'synthetic'); END")
            db.commit()
        with self.assertRaises(ConversationError): Store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='message_pending_transcripts'").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM messages").fetchone()[0],2)
            db.execute("DROP TRIGGER fail_migration"); db.commit()
        self.store=Store(self.path)
        self.assertEqual(len(self.store.messages(self.aid,self.cid)["items"]),2)

    def test_archive_gap_is_not_concatenated_or_claimed_complete(self):
        self.ingest(range(91,101),True)
        self.ingest(range(1,11),False,message(11)["_key"])
        latest = self.store.messages(self.aid,self.cid,size=10)
        self.assertEqual(len(latest["items"]),10)
        gap = self.store.messages(self.aid,self.cid,latest["older_cursor"],10)
        self.assertTrue(gap["needs_sync"])
        self.assertNotEqual(gap["coverage"],"complete")
        self.assertEqual(self.store.history_body(self.aid,self.cid,gap["older_cursor"],10)["boundary"],message(91)["_key"])

    def test_pending_voice_older_than_recent_window_is_rechecked(self):
        items=[message(i) for i in range(1,101)]
        items[0].update(type="voice",content=dict(transcript=None,transcript_status="not_available"))
        group=dict(shard="message/message_0.db",table_name="Msg_test",generation="g",source_id="synthetic_0",last_id=100,signature=[1],items=items)
        self.store.save_delta(self.aid,dict(groups=[group]))
        self.assertIn(1,self.store.states(self.aid)[0]["reconcile_ids"])
        items[0]["content"].update(transcript="existing transcription",transcript_status="available")
        group["items"]=[items[0]]
        self.store.save_delta(self.aid,dict(groups=[group]))
        self.assertNotIn(1,self.store.states(self.aid)[0]["reconcile_ids"])

    def test_failed_shard_does_not_advance_history_boundary(self):
        self.ingest([5],True,issues=["message_shard_missing"])
        page = self.store.messages(self.aid,self.cid)
        self.assertTrue(page["needs_sync"])
        self.assertIsNone(self.store.history_body(self.aid,self.cid,page["older_cursor"],50)["boundary"])

    def test_message_checkpoint_and_notification_commit_atomically(self):
        notifications=[]; self.store.notify=notifications.append
        group=dict(shard="message/message_0.db",table_name="Msg_test",generation="g",source_id="synthetic_0",last_id=2,signature=[1],items=[message(1),message(2)])
        def fail():
            self.store.db.execute("CREATE TRIGGER fail_checkpoint BEFORE INSERT ON sync_state BEGIN SELECT RAISE(ABORT,'synthetic'); END")
            self.store.db.commit()
        self.store.call(fail)
        with self.assertRaises(ConversationError): self.store.save_delta(self.aid,dict(groups=[group]))
        self.assertEqual(self.store.messages(self.aid,self.cid)["items"],[])
        self.assertEqual(self.store.states(self.aid),[])
        self.assertEqual(notifications,[])
        self.store.call(lambda:self.store.db.execute("DROP TRIGGER fail_checkpoint"))
        self.store.save_delta(self.aid,dict(groups=[group]))
        self.assertEqual(len(self.store.messages(self.aid,self.cid)["items"]),2)
        self.assertEqual(self.store.states(self.aid)[0]["last_id"],2)
        self.assertEqual(len(notifications),1)

    def test_reset_invalidates_coverage_without_deleting_archive(self):
        self.ingest([1,2])
        self.store.save_delta(self.aid,dict(groups=[dict(shard="message/message_0.db",table_name="Msg_test",generation="new",source_id="synthetic_0",last_id=0,signature=[2],items=[],reset=True)]))
        page = self.store.messages(self.aid,self.cid)
        self.assertEqual(len(page["items"]),2)
        self.assertTrue(page["needs_sync"])

    def test_image_lru_is_bounded_and_text_survives(self):
        self.ingest([1,2]); self.store.image_limit=10
        mids=[m["message_id"] for m in self.store.messages(self.aid,self.cid)["items"]]
        for mid in mids:
            self.store.image(self.aid,self.cid,mid,dict(status="available",mime_type="image/png",data_base64=base64.b64encode(b"12345678").decode(),quality="thumbnail"))
        self.assertTrue(self.store.image(self.aid,self.cid,mids[0])["needs_sync"])
        self.assertFalse(self.store.image(self.aid,self.cid,mids[1])["needs_sync"])
        self.assertEqual(len(self.store.messages(self.aid,self.cid)["items"]),2)

    def test_single_owner_and_interrupted_jobs(self):
        with self.assertRaisesRegex(ConversationError,"数据库已由"):
            Store(self.path)
        self.store.job("interrupted",dict(task_id="interrupted",account_id=self.aid,status="running"),"history")
        self.store.close(); self.store=Store(self.path)
        self.assertEqual(self.store.job("interrupted")["error"],"sync_interrupted")

    def test_migrate_preferences_once_and_preserve_damaged_input(self):
        path=Path(self.temp.name)/"old.json"
        old=ConversationSettingsStore(path)
        old.save("owner",Exclusions(wechat_pay=False))
        original=path.read_bytes()
        migrated=Store(Path(self.temp.name)/"migration.sqlite3",old)
        try: self.assertFalse(migrated.settings("owner")["wechat_pay"])
        finally: migrated.close()
        self.assertEqual(path.read_bytes(),original)
        path.write_text("broken",encoding="utf-8")
        with self.assertRaises(SettingsError): Store(Path(self.temp.name)/"broken.sqlite3",old)
        self.assertEqual(path.read_text(),"broken")

    def test_offline_api_queries_do_not_touch_source(self):
        self.ingest([1,2]); self.store.close()
        reader=Mock(); probe=Mock(side_effect=AssertionError("source called"))
        with TestClient(create_app(probe,conversation_reader=reader,database_path=self.path,monitor=False),base_url="http://127.0.0.1:8765") as client:
            for route, body in [("accounts/query",{}),("conversations/query",dict(account_id=self.aid)),("messages/query",dict(account_id=self.aid,conversation_id=self.cid))]:
                self.assertEqual(client.post("/api/v1/"+route,json=body).status_code,200)
            probe.assert_not_called(); reader.read.assert_not_called(); reader.validate.assert_not_called(); reader.messages.assert_not_called()
        self.store=Store(self.path)

    def test_websocket_json_subscription_isolation_and_invalid_origin(self):
        self.store.close()
        app=create_app(accounts,conversation_reader=Reader(),database_path=self.path,monitor=False)
        with TestClient(app,base_url="http://127.0.0.1:8765") as client:
            with self.assertRaises(Exception):
                with client.websocket_connect("ws://127.0.0.1:8765/api/v1/events",headers={"origin":"https://evil.example"}): pass
            with client.websocket_connect("ws://127.0.0.1:8765/api/v1/events",headers={"origin":"http://127.0.0.1:5173"}) as socket:
                socket.send_json(dict(type="subscribe",account_ids=[self.aid]))
                self.assertEqual(socket.receive_json()["type"],"ready")
                app.state.store.set_state(self.aid,"offline")
                event=socket.receive_json()
                self.assertEqual(event["account_id"],self.aid)
                self.assertNotIn("wxid",json.dumps(event))
        self.store=Store(self.path)


class CoordinatorTests(unittest.TestCase):
    def test_server_timer_runs_without_a_browser_and_never_overlaps_source_reads(self):
        store=Store(":memory:");reader=Reader()
        active=0;maximum=0;calls=0;observed=threading.Event()
        def change(*args):
            nonlocal active,maximum,calls
            active+=1;maximum=max(maximum,active);calls+=1
            time.sleep(.025)
            active-=1
            if calls>=4:observed.set()
            return dict(groups=[],unchanged=True)
        reader.changes=change
        with patch("wechat_account_probe.native.enumerate_instances",return_value=([],[])):
            bridge=Bridge(store,accounts,reader,monitor=True,interval=.01)
            try:
                self.assertTrue(observed.wait(3))
                self.assertEqual(maximum,1)
                self.assertLessEqual(len(bridge.queue.pending),1)
            finally:bridge.close()

    def test_failed_job_write_does_not_wedge_the_source_queue(self):
        store=Mock();store.job.side_effect=ConversationError("storage_unavailable","synthetic",503)
        queue=WorkQueue(store)
        try:
            with self.assertRaises(ConversationError):
                queue.submit("work",dict(task_id="t",status="queued"),"history",Mock())
            self.assertEqual(queue.pending,{})
        finally: queue.close()

    def test_background_source_work_is_account_scoped_and_keeps_old_data_on_failure(self):
        store=Store(":memory:"); reader=Reader()
        reader.changes=Mock(return_value=dict(groups=[],unchanged=True))
        bridge=Bridge(store,accounts,reader)
        try:
            bridge.discovery.scan()
            bridge.tick(check_processes=False)
            self.assertEqual(reader.changes.call_count,2)
            self.assertEqual(reader.calls,0)
            self.assertTrue(all(a["source_state"]=="online" for a in store.accounts()))
        finally: bridge.close()
