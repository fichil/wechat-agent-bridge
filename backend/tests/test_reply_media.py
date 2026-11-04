import base64
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from PIL import Image
from backend.app.conversations import ConversationError
from backend.app.reply_media import parse_reply, decode_base64, validate_media
from backend.app.media_schema import upgrade_v11, MEDIA_SCHEMA
from backend.app.storage import Store
from backend.app.delivery_storage import split_reply
from backend.app.delivery import DeliveryExecution
from backend.app.diagnostics import sanitize
from wechat_sender import media_files
from test_roles import RoleTestCase
import test_delivery as delivery
from migration_helpers import remove_v11


def picture(animated=False, size=(8,8)):
    out=io.BytesIO(); first=Image.new('RGBA',size,'red')
    if animated: first.save(out,format='GIF',save_all=True,append_images=[Image.new('RGBA',size,'blue')],duration=[80,160],loop=0)
    else: first.save(out,format='PNG')
    return out.getvalue()


def encoded(data=None): return base64.b64encode(data or picture()).decode()


class ReplyGrammarTests(unittest.TestCase):
    def test_split_only_standalone_empty_parts_and_newlines(self):
        self.assertEqual(parse_reply('第一行\n第二行\r\n [[SPLIT]] \r\n\n[[SPLIT]]\n末条'),
            [dict(kind='text',text='第一行\n第二行'),dict(kind='text',text='末条')])
        self.assertEqual(parse_reply('[[SPLIT]]\n\n'),[])
        for bad in ('a[[SPLIT]]b','x\n[[STICKER:id]]','[[IMAGE:../../path]]','[[IMAGE:bad', '[[STICKER:]]','[[IMAGE:data:image/png;base64,@]]'):
            with self.subTest(bad=bad), self.assertRaises(ConversationError): parse_reply(bad)

    def test_long_chinese_and_combined_emoji(self):
        text=('中文👨‍👩‍👧‍👦👍🏽🇨🇳'*200)+'结束'
        parts=split_reply(parse_reply(text)[0]['text'])
        self.assertEqual(''.join(parts),text)
        self.assertTrue(all(len(p.encode())<=1000 for p in parts))
        self.assertTrue(all(not p.endswith('\u200d') for p in parts))

    def test_strict_base64_and_mime_damage_limits(self):
        for bad in ('','YW Jj','%%%%','AAAA=AAAA','你'):
            with self.subTest(bad=bad), self.assertRaises(ConversationError): decode_base64(bad)
        for data,mime in ((b'not a picture','image/png'),(picture(),'image/jpeg'),(picture()[:30],'image/png')):
            with self.assertRaises(ConversationError): validate_media(data,mime=mime)
        with self.assertRaises(ConversationError): decode_base64('A'*(12*1024*1024))
        with self.assertRaises(ConversationError): validate_media(picture(size=(1025,2)),'sticker')
        for raw in (encoded(),'前文 data:image/png;base64,'+encoded(),'data:image/png;base64,@@bad'):
            with self.assertRaises(ConversationError): parse_reply(raw)
        self.assertNotIn(encoded(),sanitize(encoded()))

    def test_static_converted_and_gif_animation_preserved(self):
        result=validate_media(picture(),'sticker','image/png')
        self.assertEqual((result['mime_type'],result['frame_count']),('image/gif',1))
        with Image.open(io.BytesIO(result['data'])) as im: self.assertEqual(im.n_frames,1)
        data=picture(True); gif=validate_media(data,'sticker','image/gif')
        self.assertEqual(gif['data'],data);self.assertEqual(gif['frame_count'],2)
        with self.assertRaises(ConversationError): validate_media(data,'image')
        part=parse_reply('[[STICKER:data:image/png;base64,'+encoded()+']]')[0]
        self.assertEqual(part['media']['mime_type'],'image/gif')

    def test_worker_staging_rejects_paths_and_mutation(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(media_files,'ROOT',Path(folder)):
            media=validate_media(picture())
            request=media_files.stage('a'*32,media)
            path=media_files.resolve(request)
            with self.assertRaises(ValueError): media_files.resolve(dict(request,media_token='../escape.png'))
            path.write_bytes(b'changed')
            with self.assertRaises(ValueError): media_files.resolve(request)
            path.write_bytes(media['data']);media_files.release(request)
            self.assertFalse(path.exists())

    def test_logs_never_keep_valid_or_malformed_media_payloads(self):
        for payload in (encoded(),encoded()+'@@bad',encoded()+'\\nBAD'):
            value=sanitize(dict(reply='[[IMAGE:data:image/png;base64,'+payload+']]'))
            self.assertNotIn(payload,json.dumps(value))
            self.assertIn('omitted',value['reply'])


class ReplyMediaStoreTests(RoleTestCase):
    configured=delivery.DeliveryTests.configured
    delta=delivery.DeliveryTests.delta
    start=delivery.DeliveryTests.start
    wait_run=delivery.DeliveryTests.wait_run

    def prepare(self):
        self.configured();self.update(self.preferences()['revision'],ai_max_retries=0)
        self.delta()

    def reply(self,value,hook=None):
        def generate(*args):
            if hook: hook()
            return dict(memory='新的记忆',reply=value)
        with patch.object(self.provider,'generate',side_effect=generate): return self.wait_run(self.start())

    def custom(self):
        return self.post('stickers/create',name='测试图片',description='高兴时使用',mime_type='image/png',data_base64=encoded(),enabled=True).json()

    def test_library_revision_validation_and_no_binary_in_list(self):
        self.assertEqual(len(self.post('stickers/query').json()['items']),27)
        value=self.custom();aid=value['asset_id']
        self.assertNotIn('data_base64',value['media'])
        self.assertEqual(self.post('stickers/get',asset_id=aid).json()['media']['mime_type'],'image/gif')
        result=self.post('stickers/update',asset_id=aid,expected_revision=1,name='新名',description='新描述',enabled=False)
        self.assertEqual(result.json()['revision'],2)
        self.assertEqual(self.post('stickers/delete',asset_id=aid,expected_revision=1).status_code,409)
        self.assertEqual(self.post('stickers/delete',asset_id=aid,expected_revision=2).status_code,200)
        self.assertEqual(self.post('stickers/get',asset_id=aid).status_code,404)
        self.assertEqual(self.post('stickers/create',name='bad',description='bad',mime_type='image/png',data_base64='bad').status_code,422)

    def test_mixed_reply_snapshot_survives_delete_and_cache_eviction(self):
        self.prepare();asset=self.custom()
        reply='第一行\n普通换行\n[[SPLIT]]\n[[STICKER:'+asset['asset_id']+']]\n[[SPLIT]]\n[[IMAGE:data:image/png;base64,'+encoded()+']]\n[[SPLIT]]\n末条'
        run=self.reply(reply,lambda:self.store.delete_sticker(asset['asset_id'],1))
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual([p['kind'] for p in run['reply_parts']],['text','sticker','image','text'])
        self.assertEqual(run['delivery']['total_parts'],4)
        self.assertNotIn('base64',json.dumps(run))
        self.assertEqual(self.store.reply_media(self.ids[0],self.cids[0],run['run_id'],1)['sha256'],asset['media']['sha256'])
        self.assertEqual(len(self.store.delivery_candidates()),1)
        job=self.store.claim_delivery(run['run_id'],0)
        self.assertIsNone(self.store.claim_delivery(run['run_id'],0))
        self.store.finish_delivery(job,dict(status='sent'))
        self.assertEqual(self.store.delivery_candidates()[0]['part_index'],1)
        job=self.store.claim_delivery(run['run_id'],1)
        self.store.finish_delivery(job,dict(status='sent')) # No completion/archival evidence.
        self.assertEqual(self.store.delivery_candidates(),[])
        self.assertEqual(self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])['delivery']['status'],'unknown')
        self.store.close();reopened=Store(self.path)
        try:
            self.assertEqual(reopened.delivery_candidates(),[])
            self.assertEqual(reopened.reply_media(self.ids[0],self.cids[0],run['run_id'],1)['sha256'],asset['media']['sha256'])
        finally: reopened.close()

    def test_whole_batch_validation_precedes_memory_and_queue_commit(self):
        self.prepare()
        before=self.preferences()['memory_revision']
        run=self.reply('不应先发送\n[[SPLIT]]\n[[STICKER:missing]]')
        self.assertEqual(run['status'],'failed',run)
        self.assertEqual(self.preferences()['memory_revision'],before)
        self.assertEqual(run['delivery']['total_parts'],0)
        self.assertEqual(self.store.call(lambda:self.store.db.execute('SELECT count(*) FROM reply_candidates').fetchone()[0]),0)

    def test_image_candidates_are_account_conversation_scoped_and_frozen(self):
        self.prepare()
        item=self.delta(2,type='image',content={})
        mid=self.store.messages(self.ids[0],self.cids[0])['items'][-1]['message_id']
        self.store.image(self.ids[0],self.cids[0],mid,dict(status='available',mime_type='image/png',data_base64=encoded()))
        def evict():
            def write():
                with self.store.db: self.store.db.execute('DELETE FROM media_cache')
            self.store.call(write)
        run=self.reply('[[IMAGE:'+mid+']]',evict)
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(run['reply_parts'][0]['kind'],'image')
        with self.assertRaises(ConversationError): self.store.reply_media(self.ids[1],self.cids[1],run['run_id'],0)
        # Another account may not reference that image even though outgoing blobs exist globally.
        self.update(index=1,ai_enabled=True,ai_max_retries=0);self.delta(3,account=1)
        with patch.object(self.provider,'generate',return_value=dict(memory='bad',reply='[[IMAGE:'+mid+']]')):
            other=self.wait_run(self.start(account=1))
        self.assertEqual(other['status'],'failed',other)
        self.assertEqual(self.preferences(1)['memory_revision'],0)

    def test_native_evidence_must_match_self_type_target_and_current_archive(self):
        self.prepare();run=self.reply('[[STICKER:data:image/gif;base64,'+encoded(picture(True))+']]\n[[SPLIT]]\n后续')
        self.assertEqual(run['status'],'completed',run)
        job=self.store.claim_delivery(run['run_id'],0)
        evidence=dict(token='f'*32,local_id=3,create_time=1,timestamp_ms=1000,source_uuid='12345678-1234-1234-1234-123456789abc',message_type=47,target=job['target'])
        self.assertIsNone(self.store.corroborate_delivery(job,evidence))
        item=self.delta(3,type='sticker',is_self=True,content={})
        created=int(datetime.fromisoformat(item['sent_at']).timestamp())
        evidence.update(local_id=item['_ref']['local_id'],create_time=created,timestamp_ms=created*1000)
        # Use a synthetic server identity matching only this completed operation.
        def assign():
            with self.store.db: self.store.db.execute('UPDATE messages SET server_id=? WHERE local_id=? AND account_id=?',('999',item['_ref']['local_id'],self.ids[0]))
        self.store.call(assign)
        archived=self.store.corroborate_delivery(job,evidence)
        self.assertIsNotNone(archived)
        self.assertIsNone(self.store.corroborate_delivery(job,dict(evidence,target='other')))
        self.assertIsNone(self.store.corroborate_delivery(job,dict(evidence,local_id=9999)))
        self.assertIsNone(self.store.corroborate_delivery(job,dict(evidence,create_time=created-1)))
        self.store.finish_delivery(job,dict(status='sent',evidence=evidence))
        readback=self.store.image(self.ids[0],self.cids[0],archived)
        self.assertFalse(readback['needs_sync'])
        self.assertEqual(readback['data_base64'],encoded(picture(True)))
        self.assertEqual(self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])['delivery']['parts'][0]['confirmation'],'native_archive')
        response=self.post('ai-runs/query',account_id=self.ids[0],conversation_id=self.cids[0],run_id=run['run_id'])
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.store.delivery_candidates()[0]['part_index'],1)

    def test_dispatch_stops_at_media_failure_and_retries_only_unsent_item(self):
        self.prepare();run=self.reply('前文\n[[SPLIT]]\n[[STICKER:data:image/png;base64,'+encoded()+']]\n[[SPLIT]]\n后文')
        sent=[]
        class Transport:
            def inspect(self,binding): return dict(ready=True,send_text=True,send_image=True,send_sticker=True)
            def send(self,binding,job):
                sent.append((job['part_index'],job['kind']))
                if job['kind']=='sticker':
                    media_files.resolve(job['staged_media'])
                    return dict(status='failed',code='native_rejected',resources_released=True)
                return dict(status='sent')
        executor=DeliveryExecution(self.store,self.app.state.bridge,Transport(),automatic=False)
        try:
            with patch.object(executor,'binding',return_value=dict(account='synthetic',pid=1,created=1)), patch.object(media_files,'ROOT',Path(self.temp.name)/'outgoing'):
                executor.perform(self.store.delivery_candidates()[0])
                executor.perform(self.store.delivery_candidates()[0])
                self.assertEqual(sent,[(0,'text'),(1,'sticker')])
                self.assertEqual(self.store.delivery_candidates(),[])
                self.assertEqual(list(media_files.ROOT.iterdir()),[])
                self.store.update_delivery(self.ids[0],self.cids[0],run['run_id'])
                self.assertEqual(self.store.delivery_candidates()[0]['part_index'],1)
                executor.perform(self.store.delivery_candidates()[0])
                self.assertEqual(sent[-1],(1,'sticker'))
        finally: executor.close()

    def test_disabled_and_switched_configuration_do_not_mutate_frozen_reply(self):
        self.prepare()
        run=self.reply('[[STICKER:data:image/png;base64,'+encoded()+']]')
        self.update(self.preferences()['revision'],ai_enabled=False)
        self.assertEqual(self.store.delivery_candidates(),[])
        self.assertEqual(self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])['delivery']['status'],'cancelled')
        self.assertEqual(self.store.reply_media(self.ids[0],self.cids[0],run['run_id'],0)['mime_type'],'image/gif')

    def test_v10_upgrade_and_ddl_failure_are_atomic(self):
        self.store.close()
        with closing(sqlite3.connect(self.path)) as db:
            remove_v11(db);db.execute('PRAGMA user_version=10');db.commit()
            with patch('backend.app.media_schema.MEDIA_SCHEMA',MEDIA_SCHEMA+('INVALID SQL',)):
                with self.assertRaises(sqlite3.OperationalError): upgrade_v11(db)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],10)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='stickers'").fetchone())
        reopened=Store(self.path)
        try:
            self.assertEqual(len(reopened.stickers()['items']),27)
            self.assertEqual(reopened.call(lambda:reopened.db.execute('PRAGMA user_version').fetchone()[0]),12)
        finally: reopened.close()
