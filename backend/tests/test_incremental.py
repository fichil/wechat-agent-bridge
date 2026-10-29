from pathlib import Path
from contextlib import contextmanager
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock

from backend.app.storage import Store
from backend.app.services import browser_values
from test_conversations import accounts
from test_messages import MemoryCache, shard_image
from wechat_messages.reader import MessageReader
from wechat_account_probe.models import ProbeError


class CountingCache(MemoryCache):
    def __init__(self, images):
        super().__init__(images)
        self.entries={}
        self.calls=0

    @contextmanager
    def database(self,name):
        self.calls+=1
        with super().database(name) as db:
            yield db


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.source="synthetic@chatroom"
        self.shard="message/message_0.db"
        self.session=Mock(directory=self.root,wxid="self",cache=None)
        self.session.read.return_value=dict(items=[],issues=[],status="complete")
        self.reader=MessageReader(self.session)
        self.reader.shards=lambda:[self.shard]
        db=sqlite3.connect(":memory:")
        db.execute('CREATE TABLE contact(username TEXT,alias TEXT,nick_name TEXT,remark TEXT)')
        db.execute("INSERT INTO contact VALUES('self',NULL,'合成',NULL)")
        contacts=db.serialize();db.close()
        db=sqlite3.connect(":memory:");db.execute("CREATE TABLE SessionTable(username TEXT)")
        db.execute("INSERT INTO SessionTable VALUES(?)",(self.source,));sessions=db.serialize();db.close()
        self.reader.cache=CountingCache({"contact/contact.db":contacts,"session/session.db":sessions})
        self.store=Store(":memory:")
        self.aid=self.store.save_accounts(browser_values(accounts()))["accounts"][0]["account_id"]
        self.write(range(1,4))

    def tearDown(self):
        self.store.close();self.temp.cleanup()

    def write(self, ids, zero_server=False):
        values=[(i,0 if zero_server else i+1000,1,1000-i,1,1780000000,f"message-{i}",None,None) for i in ids]
        self.reader.cache.images[self.shard]=shard_image(self.source,values,[(1,"self")])
        path=self.root/"db_storage"/self.shard
        path.parent.mkdir(parents=True,exist_ok=True)
        self.write_count=getattr(self,"write_count",0)+1
        # Size changes deterministically, even on filesystems with coarse mtime updates.
        path.write_bytes(b"synthetic"+b"x"*self.write_count)

    def tick(self):
        result=self.reader.changes(dict(states=self.store.states(self.aid)),time.monotonic()+60)
        self.store.save_delta(self.aid,result)
        return result

    def messages(self):
        conv=self.store.call(lambda:self.store.db.execute("SELECT conversation_id FROM conversations WHERE source_id=?",(self.source,)).fetchone())
        return self.store.messages(self.aid,conv[0],size=100)["items"] if conv else []

    def test_baseline_only_then_no_change_performs_zero_decryption_or_binding(self):
        self.tick();self.assertEqual(self.messages(),[])
        calls=self.reader.cache.calls;binds=self.session.bind.call_count
        for _ in range(3): self.assertTrue(self.tick()["unchanged"])
        self.assertEqual(self.reader.cache.calls,calls)
        self.assertEqual(self.session.bind.call_count,binds)

    def test_unopened_conversation_new_rows_are_ingested_despite_lower_sort_values(self):
        self.tick();self.write(range(1,9));self.tick()
        self.assertEqual(len(self.messages()),5)
        self.assertEqual({m["content"]["text"] for m in self.messages()},{f"message-{i}" for i in range(4,9)})
        self.tick();self.assertEqual(len(self.messages()),5)

    def test_hidden_chat_absent_from_sessions_and_contacts_resolves_from_name2id(self):
        db=sqlite3.connect(":memory:")
        db.execute("CREATE TABLE SessionTable(username TEXT)")
        self.reader.cache.images["session/session.db"]=db.serialize();db.close()
        for ids in (range(1,4),range(1,6)):
            self.write(ids)
            db=sqlite3.connect(":memory:");db.deserialize(self.reader.cache.images[self.shard])
            db.execute("INSERT INTO Name2Id(rowid,user_name) VALUES(2,?)",(self.source,))
            db.commit()
            self.reader.cache.images[self.shard]=db.serialize();db.close()
            self.assertNotIn("monitor_source_unresolved",self.tick()["issues"])
        self.assertEqual(len(self.messages()),2)

    def test_more_than_one_batch_and_restart_resume_without_advancing_past_pending(self):
        self.tick();self.write(range(1,209));first=self.tick()
        self.assertLessEqual(sum(len(g.get("items",[])) for g in first["groups"]),100)
        state=next(s for s in self.store.states(self.aid) if s["table_name"])
        self.assertEqual(state["last_id"],103);self.assertEqual(state["upper_id"],208)
        # A fresh reader has no remembered cursor; persisted checkpoints are sufficient.
        delattr(self.reader,"next_shard")
        for _ in range(6): self.tick()
        count=self.store.call(lambda:self.store.db.execute("SELECT count(*) FROM messages").fetchone()[0])
        self.assertEqual(count,205)
        self.assertTrue(self.tick()["unchanged"])

    def test_failed_shard_retains_watermark_and_recovers(self):
        self.tick();self.write(range(1,6));image=self.reader.cache.images[self.shard]
        self.reader.cache.images[self.shard]=ProbeError("database_page_hmac_failed")
        result=self.tick();self.assertIn("database_page_hmac_failed",result["issues"])
        self.assertEqual(next(s for s in self.store.states(self.aid) if s["table_name"])["last_id"],3)
        self.reader.cache.images[self.shard]=image;self.tick();self.assertEqual(len(self.messages()),2)

    def test_checkpoint_stamp_change_does_not_reimport_or_reset_baseline(self):
        self.tick();self.write(range(1,4));self.tick()
        self.assertEqual(self.messages(),[])
        self.assertEqual(next(s for s in self.store.states(self.aid) if s["table_name"])["baseline_id"],3)

    def test_local_id_regression_creates_generation_without_overwriting_zero_server_ids(self):
        self.write(range(1,4),True);self.tick();self.write(range(1,6),True);self.tick()
        old=next(s for s in self.store.states(self.aid) if s["table_name"])["generation"]
        self.write(range(1,3),True);self.tick()
        new=next(s for s in self.store.states(self.aid) if s["table_name"])["generation"]
        self.assertNotEqual(old,new)
        self.assertEqual(len(self.messages()),4)
