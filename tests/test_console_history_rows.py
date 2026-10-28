"""Synthetic pagination, ordering and unknown identity regression tests."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests import test_bound_message_labels as label_fixtures
from tests.test_console import SyntheticProtector, Clock
from wechat_agent_bridge.adapters.bound_read import collect_labels, console_message_page
from wechat_agent_bridge.adapters.console_mock import MockConsoleAdapter
from wechat_agent_bridge.console_models import Account, Conversation, ChatMessage, ConsoleError, MessagePage
from wechat_agent_bridge.console_store import ConsoleStore, digest
from wechat_agent_bridge.console_service import ConsoleService


class HistoryRowsTests(unittest.TestCase):
    def test_all_loaded_pages_have_no_omissions_or_duplicates(self):
        raw, root, uia, listing, items = label_fixtures.BoundMessageLabelTests().fixture(125)
        captured = []; cursor = None
        for _ in range(3):
            page = collect_labels(raw, root, uia, 7, cursor)
            captured[0:0] = [row['runtime_id'][-1] for row in page['rows']]
            cursor = page['cursor']
        self.assertIsNone(cursor)
        self.assertEqual(captured, list(range(125)))
        self.assertEqual(page['loaded_start'], 0)

    def test_history_anchor_change_and_wrong_list_rejected(self):
        raw, root, uia, listing, items = label_fixtures.BoundMessageLabelTests().fixture(80)
        cursor = collect_labels(raw, root, uia, 7)['cursor']
        for change in ({'list':[0]}, {'row':[7,99]}, {'digest':'changed'}):
            bad = json.dumps({**json.loads(cursor), **change})
            with self.assertRaisesRegex(ConsoleError, 'history_cursor_changed'):
                collect_labels(raw, root, uia, 7, bad)

    def test_all_row_types_kept_without_guessing_sender_or_attachment_content(self):
        a = Account('a','synthetic'); c = Conversation('a','c','group','group')
        rows = [dict(runtime_id=[42,7,4,-10-i],label='wxid_fake_sender',class_name=cls)
                for i, cls in enumerate(('mmui::ChatTextItemView','mmui::ChatBubbleReferItemView','unknown'))]
        page = console_message_page(dict(rows=rows,loaded_count=3,loaded_start=0,loaded_end=3),a,c,7)
        self.assertEqual(len(page.messages), 3)
        self.assertFalse(page.complete)
        for message in page.messages:
            self.assertIsNone(message.sender_wechat_id)
            self.assertIsNone(message.sender_remark)
            self.assertIsNone(message.own)
            self.assertTrue(message.historical)
        self.assertTrue(page.messages[0].upload_eligible)
        self.assertFalse(page.messages[1].upload_eligible)
        self.assertEqual(page.messages[1].content_status, 'client_label_only')
        self.assertFalse(page.messages[1].attachments)

    def test_sender_fields_roundtrip_separate_from_display_name(self):
        message = ChatMessage('a','c','1','same name','text','',sender_remark='remark',
                              sender_wechat_id='wxid_synthetic',sender_identity_source='synthetic_profile')
        self.assertEqual(ChatMessage.from_dict(message.to_dict()), message)
        old = message.to_dict()
        for key in ('sender_remark','sender_wechat_id','sender_identity_source','content_status'):
            old.pop(key)
        self.assertIsNone(ChatMessage.from_dict(old).sender_wechat_id)

    def test_history_order_does_not_change_incremental_watermark_or_repeat_events(self):
        store = ConsoleStore(':memory:', SyntheticProtector()); self.addCleanup(store.close)
        def add(i, older=False):
            return store.append(ChatMessage('a','c',str(i),'s',str(i),''), older=older)
        add(3); add(4)
        watermark = store.messages('a','c')[-1]['seq']
        add(2,True); add(1,True); add(5)
        rows = store.messages('a','c')
        self.assertEqual([r['text'] for r in rows], ['1','2','3','4','5'])
        self.assertFalse(add(2,True))
        self.assertEqual([r['text'] for r in store.messages('a','c',before=rows[2]['seq'])], ['1','2'])
        self.assertEqual([r['text'] for r in store.messages('a','c',after=watermark)], ['2','1','5'])
        self.assertEqual(store.messages('other','c',before=rows[2]['seq']), [])

    def test_old_database_migration_preserves_encrypted_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'state.db'; protector = SyntheticProtector()
            payload = protector.protect(json.dumps(ChatMessage('a','c','id','s','text','').to_dict()).encode())
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE messages(seq INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT, conversation TEXT, event TEXT UNIQUE, payload BLOB NOT NULL)')
            db.execute('INSERT INTO messages VALUES(7,?,?,?,?)',(digest('a'),digest('a','c'),'event',payload));db.commit();db.close()
            store=ConsoleStore(path,protector)
            self.assertEqual(store.db.execute('SELECT payload FROM messages').fetchone()[0],payload)
            self.assertEqual(store.messages('a','c')[0]['display_order'],7);store.close()

    def test_service_does_not_claim_complete_or_upload_label_only_rows(self):
        store=ConsoleStore(':memory:',SyntheticProtector());self.addCleanup(store.close)
        adapter=MockConsoleAdapter();service=ConsoleService(adapter,store,clock=Clock())
        service.discover();service.authorize('mock-a',dict(read=True,upload=True,reply=False,paused=False))
        service.conversations('mock-a')
        label=ChatMessage('mock-a','friend','opaque','unknown','label','',upload_eligible=False,content_status='client_label_only')
        adapter.messages=lambda *args:MessagePage((label,),complete=False,loaded_count=1)
        service.sync('mock-a','friend')
        with self.assertRaisesRegex(ConsoleError,'client_history_loading_unsupported'):
            service.sync('mock-a','friend',older=True)
        service.resume()
        self.assertEqual(service._context('mock-a','friend',service.epoch,store.messages('mock-a','friend')),[])
