from contextlib import closing
from datetime import datetime, timezone, timedelta
import json
import sqlite3
import time
from unittest.mock import patch
import zstandard
from test_roles import RoleTestCase
import test_ai_execution as execution
from test_message_api import message
from test_conversations import rows
from backend.app.storage import Store
from backend.app.delivery_storage import DELIVERY_SCHEMA, split_reply
from backend.app.conversations import ConversationError
from wechat_messages.content import parse_mentions
from migration_helpers import remove_v7


class DeliveryTests(RoleTestCase):
    configured=execution.ExecutionTests.configured
    ingest=execution.ExecutionTests.ingest
    start=execution.ExecutionTests.start
    wait_run=execution.ExecutionTests.wait_run

    def delta(self,index=1,account=0,**changes):
        item={**message(index),'is_self':False,'sent_at':(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat(),**changes}
        item['_ref']['generation']='g'
        aid,cid=self.ids[account],self.cids[account]
        source=self.store.call(lambda:self.store._conversation(aid,cid)['source_id'])
        self.store.save_delta(aid,dict(groups=[dict(source_id=source,shard=item['_ref']['shard'],table_name='Msg_test',generation='g',last_id=index,baseline_id=0,items=[item])],issues=[]))
        return item

    def finish(self,run,status='sent'):
        jobs=self.store.delivery_candidates()
        job=next(j for j in jobs if j['run_id']==run['run_id'])
        claimed=self.store.claim_delivery(job['run_id'],job['part_index'])
        self.store.finish_delivery(claimed,dict(status=status,code='synthetic'))
        return claimed

    def test_private_live_reply_is_atomic_and_duplicate_proof(self):
        self.configured(); item=self.delta()
        self.assertEqual(self.store.ai_candidates(),[]) # debounce
        run=self.wait_run(self.start())
        self.assertEqual(run['delivery']['status'],'queued')
        self.assertTrue(run['reply'])
        self.assertEqual(self.store.ai_candidates(),[]) # one conversation pipeline
        self.finish(run)
        self.delta(content={'text':'corrected'})
        second=self.wait_run(self.start())
        self.assertEqual(second['delivery']['status'],'not_required')
        self.assertEqual(second['reply'],'')
        self.assertEqual(len(self.store.call(lambda:self.store.db.execute('SELECT * FROM deliveries').fetchall())),1)

    def test_history_old_timestamp_and_self_do_not_send(self):
        self.configured(); self.ingest()
        self.assertEqual(self.wait_run(self.start())['delivery']['status'],'not_required')
        self.delta(2,sent_at='2020-01-01T00:00:00Z')
        self.assertEqual(self.wait_run(self.start())['delivery']['status'],'not_required')
        self.delta(3,is_self=True)
        self.assertEqual(self.wait_run(self.start())['delivery']['status'],'not_required')

    def test_live_eligibility_arriving_after_history_memory_is_not_lost(self):
        self.configured()
        when=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
        self.ingest(sent_at=when)
        self.assertEqual(self.wait_run(self.start())['delivery']['status'],'not_required')
        self.delta(sent_at=when)
        self.assertEqual(self.wait_run(self.start())['delivery']['status'],'queued')

    def test_live_eligibility_during_memory_call_goes_to_next_round(self):
        self.configured()
        when=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
        self.ingest(sent_at=when)
        original=self.provider.generate
        def generate(*args):
            self.delta(sent_at=when)
            return original(*args)
        with patch.object(self.provider,'generate',side_effect=generate):
            first=self.wait_run(self.start())
        self.assertEqual(first['delivery']['status'],'not_required')
        self.assertEqual(self.wait_run(self.start())['delivery']['status'],'queued')

    def test_split_preserves_combined_emoji_and_exact_text(self):
        for emoji in ('👨‍👩‍👧‍👦','👍🏽','🇨🇳','1️⃣','🏳️‍🌈'):
            content='a'*999+emoji+'中文'
            parts=split_reply(content)
            self.assertEqual(''.join(parts),content)
            self.assertTrue(all(len(p.encode('utf-8'))<=1000 for p in parts))
            self.assertTrue(any(emoji in p for p in parts))

    def test_group_requires_exact_metadata_and_not_all(self):
        group={**rows(1)[0],'source_id':'synthetic@chatroom','category':'group'}
        aid=self.ids[0]
        self.store.save_conversations(aid,dict(items=[group],status='complete',issues=[]))
        self.cids[0]=self.store.call(lambda:self.store.db.execute('SELECT conversation_id FROM conversations WHERE account_id=? AND source_id=?',(aid,group['source_id'])).fetchone()[0])
        self.configured()
        for index,fields in enumerate([
            dict(content={'text':'@合成账号 hello'}),
            dict(mentioned_wxids=['wxid_synthetic_01'],mention_status='known'),
            dict(mentioned_wxids=['wxid_synthetic_0'],mention_status='known',mentions_all=True),
        ],1):
            self.delta(index,**fields)
            self.assertEqual(self.wait_run(self.start())['delivery']['status'],'not_required')
        self.delta(4,mentioned_wxids=['wxid_synthetic_0'],mention_status='known')
        self.assertEqual(self.wait_run(self.start())['delivery']['status'],'queued')

    def test_manual_answer_does_not_cancel_ai_and_next_message_is_separate(self):
        self.configured(); self.delta()
        original=self.provider.generate
        def generate(*args):
            self.delta(2,is_self=True)
            self.delta(3,content={'text':'next question'})
            return original(*args)
        with patch.object(self.provider,'generate',side_effect=generate): run=self.wait_run(self.start())
        self.assertEqual(run['delivery']['status'],'queued')
        self.finish(run)
        next_run=self.wait_run(self.start())
        self.assertEqual(next_run['delivery']['status'],'queued')

    def test_unknown_pauses_retry_is_rejected_resolution_resumes(self):
        self.configured(); self.delta(); run=self.wait_run(self.start()); self.finish(run,'unknown')
        self.delta(2)
        self.assertEqual(self.store.ai_candidates(),[])
        response=self.post('ai-deliveries/retry',account_id=self.ids[0],conversation_id=self.cids[0],run_id=run['run_id'])
        self.assertEqual(response.status_code,409)
        wrong=self.post('ai-deliveries/resolve',account_id=self.ids[1],conversation_id=self.cids[1],run_id=run['run_id'],resolution='confirmed_sent')
        self.assertEqual(wrong.status_code,404)
        response=self.post('ai-deliveries/resolve',account_id=self.ids[0],conversation_id=self.cids[0],run_id=run['run_id'],resolution='confirmed_sent')
        self.assertEqual(response.json()['status'],'sent')
        self.assertEqual(response.json()['parts'][0]['confirmation'],'user')
        self.assertEqual(self.wait_run(self.start())['delivery']['status'],'queued')

    def test_failure_retry_reuses_reply_without_model_call(self):
        self.configured();self.delta();run=self.wait_run(self.start());job=self.finish(run,'failed')
        count=len(self.provider.requests)
        self.store.update_delivery(self.ids[0],self.cids[0],run['run_id'])
        retry=self.finish(run)
        self.assertEqual(job['text'],retry['text'])
        self.assertEqual(len(self.provider.requests),count)
        value=self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])
        self.assertEqual(value['delivery']['parts'][0]['attempts'],2)

    def test_empty_reply_consumes_trigger_and_never_sends(self):
        self.configured();self.delta()
        with patch.object(self.provider,'generate',return_value={'reply':'','memory':'observed'}): run=self.wait_run(self.start())
        self.assertEqual(run['delivery']['status'],'not_required')
        self.delta(content={'text':'edited'})
        self.assertEqual(self.wait_run(self.start())['delivery']['status'],'not_required')

    def test_utf8_parts_and_partial_failure_never_resend_success(self):
        text='汉🙂\n'*600
        self.assertEqual(''.join(split_reply(text)),text)
        self.assertTrue(all(len(p.encode())<=1000 for p in split_reply(text)))
        self.configured();self.delta()
        with patch.object(self.provider,'generate',return_value={'reply':text,'memory':'generated, not sent'}): run=self.wait_run(self.start())
        self.finish(run);failed=self.finish(run,'failed')
        self.assertEqual(failed['part_index'],1)
        self.store.update_delivery(self.ids[0],self.cids[0],run['run_id'])
        retry=self.finish(run)
        self.assertEqual(retry['part_index'],1)
        first=self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])['delivery']['parts'][0]
        self.assertEqual((first['status'],first['attempts']),('sent',1))

    def test_disable_cancels_queued_but_not_submitted_and_reenable_skips_old(self):
        self.configured();self.delta()
        with patch.object(self.provider,'generate',return_value={'reply':'汉'*1000,'memory':'generated'}): run=self.wait_run(self.start())
        job=self.store.claim_delivery(run['run_id'],0)
        self.update(self.preferences()['revision'],ai_enabled=False)
        self.store.finish_delivery(job,dict(status='sent'))
        result=self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])['delivery']
        self.assertEqual(result['status'],'cancelled')
        self.assertEqual(result['parts'][0]['status'],'sent')
        self.update(self.preferences()['revision'],ai_enabled=True)
        self.delta(content={'text':'old content update'})
        self.assertEqual(self.wait_run(self.start())['delivery']['status'],'not_required')

    def test_restart_marks_only_submitted_unknown(self):
        self.configured();self.delta()
        with patch.object(self.provider,'generate',return_value={'reply':'汉'*1000,'memory':'generated'}): run=self.wait_run(self.start())
        self.store.claim_delivery(run['run_id'],0)
        self.store.close()
        with closing(Store(self.path)) as store:
            result=store.ai_run(self.ids[0],self.cids[0],run['run_id'])['delivery']
            self.assertEqual(result['status'],'unknown')
            self.assertEqual(result['parts'][1]['status'],'queued')
            self.assertEqual(store.delivery_candidates(),[])

    def test_v7_backup_and_migration_failure_leave_v6_intact(self):
        self.configured();self.ingest();run=self.wait_run(self.start());self.store.close()
        with closing(sqlite3.connect(self.path)) as db:
            remove_v7(db);db.execute('PRAGMA user_version=6');db.commit()
        with patch('backend.app.storage.DELIVERY_SCHEMA',(*DELIVERY_SCHEMA,'INVALID SQL')):
            with self.assertRaises(ConversationError): Store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],6)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='deliveries'").fetchone())
        with closing(Store(self.path)) as store:
            self.assertEqual(store.ai_run(self.ids[0],self.cids[0],run['run_id'])['delivery']['status'],'not_required')
            self.assertEqual(store.preferences(self.ids[0],self.cids[0])['memory_revision'],1)
        self.assertTrue(list((self.path.parent/'migration-backups').glob('before-v9-*.sqlite3')))

    def test_mentions_parse_plain_compressed_and_malformed(self):
        xml='<msgsource><atuserlist><![CDATA[wxid_a,wxid_ab]]></atuserlist></msgsource>'
        for value in (xml,xml.encode(),zstandard.ZstdCompressor().compress(xml.encode())):
            self.assertEqual(parse_mentions(value)['mentioned_wxids'],['wxid_a','wxid_ab'])
        self.assertTrue(parse_mentions('<msgsource><atuserlist>notify@all</atuserlist></msgsource>')['mentions_all'])
        self.assertEqual(parse_mentions('<!DOCTYPE x><msgsource/>')['mention_status'],'unknown')
        self.assertEqual(parse_mentions(None)['mention_status'],'unknown')

    def test_pre_submission_offline_waits_without_retrying_model(self):
        self.configured();self.delta();run=self.wait_run(self.start())
        count=len(self.provider.requests)
        job=self.store.claim_delivery(run['run_id'],0)
        self.store.finish_delivery(job,dict(status='failed',code='source_offline'))
        self.assertEqual(self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])['delivery']['status'],'queued')
        self.finish(run)
        self.assertEqual(len(self.provider.requests),count)

    def test_archive_seconds_precision_correlates_without_resolving_unknown(self):
        self.configured();self.delta();run=self.wait_run(self.start())
        self.finish(run,'unknown')
        self.delta(2,is_self=True,content={'text':run['reply']},sent_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat())
        delivery=self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])['delivery']
        self.assertEqual(delivery['status'],'unknown')
        self.assertTrue(delivery['parts'][0]['archive_message_id'])

    def test_account_isolation_and_atomic_part_claim(self):
        self.configured();self.update(index=1,ai_enabled=True)
        self.delta();self.delta(account=1)
        first=self.wait_run(self.start());second=self.wait_run(self.start(account=1))
        self.assertEqual(len(self.store.delivery_candidates()),2)
        a=self.store.claim_delivery(first['run_id'],0)
        self.assertIsNone(self.store.claim_delivery(first['run_id'],0))
        self.store.finish_delivery(a,dict(status='unknown'))
        self.assertEqual([j['run_id'] for j in self.store.delivery_candidates()],[second['run_id']])

    def test_snapshot_watermark_excludes_unsynced_old_messages(self):
        self.configured()
        aid,cid=self.ids[0],self.cids[0]
        self.store.call(lambda:(self.store.db.execute('UPDATE delivery_sessions SET watermarks=? WHERE account_id=? AND conversation_id=?',
            (json.dumps({'message/message_0.db|Msg_test':{'generation':'g','upper':10}}),aid,cid)),self.store.db.commit()))
        item=message(1)
        shard=item['_ref']['shard']
        self.store.call(lambda:(self.store.db.execute('UPDATE delivery_sessions SET watermarks=? WHERE account_id=? AND conversation_id=?',
            (json.dumps({shard+'|Msg_test':{'generation':'g','upper':10}}),aid,cid)),self.store.db.commit()))
        self.delta(1)
        self.assertEqual(self.wait_run(self.start())['delivery']['status'],'not_required')
