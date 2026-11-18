import base64
from datetime import datetime
import io
import json
import threading
import time
import unittest
import wave
from unittest.mock import patch

import httpx

from backend.app.ai_execution import AiExecution
from backend.app.conversations import ConversationError
from backend.app.reply_media import parse_reply
from backend.app.storage import Store
from backend.app.voice_audio import encode_audio, make_wav, normalize_audio, silk_duration, split_voice_text
from backend.app.voice_provider import SpeechProvider, voice_for_card
from test_roles import RoleTestCase
import test_reply_media as media


WAV = make_wav(b'\0\0'*2400)


class VoiceCodecTests(unittest.TestCase):
    def test_real_codec_and_streaming_wav(self):
        audio = encode_audio(WAV)
        self.assertEqual(audio['duration_ms'],100)
        self.assertEqual(silk_duration(audio['silk']),100)
        with wave.open(io.BytesIO(audio['wav'])) as stream:
            self.assertEqual((stream.getnchannels(),stream.getframerate(),stream.getnframes()),(1,24000,2400))
        streaming = bytearray(WAV); streaming[4:8] = b'\xff'*4; streaming[40:44] = b'\xff'*4
        self.assertEqual(normalize_audio(bytes(streaming)),normalize_audio(WAV))
        for broken in (b'',WAV[:43],WAV+b'junk'):
            with self.assertRaises(ConversationError): normalize_audio(broken)
        for broken in (audio['silk'][:-1],audio['silk']+b'\0\0',b'#!SILK_V3'):
            with self.assertRaises(ConversationError): silk_duration(broken)

    def test_duration_and_text_are_bounded_without_losing_characters(self):
        with self.assertRaises(ConversationError): encode_audio(make_wav(b'\0\0'*24000*61))
        text = ('中文和 English 👨‍👩‍👧‍👦。下一句！'*40)+'结束'
        pieces = split_voice_text(text)
        self.assertEqual(''.join(pieces),text)
        self.assertTrue(all(len(p)<=160 for p in pieces))
        self.assertTrue(all(p.count('👨')==p.count('👦') for p in pieces))

    def test_voice_marker_must_be_its_own_first_line(self):
        self.assertEqual([p['kind'] for p in parse_reply('前文\n[[SPLIT]]\n[[VOICE]]\n朗读原文\n[[SPLIT]]\n后文')],['text','voice','text'])
        for text in ('[[VOICE]]','[[VOICE]]hi','前文[[VOICE]]\nhi','[[VOICE]]\n[[STICKER:id]]'):
            with self.assertRaises(ConversationError): parse_reply(text)

    def test_provider_uses_same_origin_and_key_and_native_gemini_audio(self):
        requests = []
        def handle(request):
            requests.append(request)
            if 'generateContent' in str(request.url):
                return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'inlineData':{'mimeType':'audio/L16;rate=24000','data':base64.b64encode(b'\0\0'*2400).decode()}}]}}]})
            return httpx.Response(200,content=WAV)
        provider = SpeechProvider(httpx.MockTransport(handle))
        profile = dict(protocol='openai_responses',base_url='https://same-origin.test/v1',model='chat',speech_model='speech')
        self.assertEqual(provider.synthesize(profile,'test-secret','你好','nova')['duration_ms'],100)
        self.assertEqual(str(requests[0].url),'https://same-origin.test/v1/audio/speech')
        self.assertEqual(requests[0].headers['Authorization'],'Bearer test-secret')
        self.assertEqual(json.loads(requests[0].content),dict(model='speech',input='你好',voice='nova',response_format='wav'))
        profile.update(protocol='gemini_generate_content',base_url='https://same-origin.test/v1beta',speech_model='gemini-2.5-flash-preview-tts')
        self.assertEqual(provider.synthesize(profile,'test-secret','你好')['duration_ms'],100)
        self.assertEqual(requests[1].headers['x-goog-api-key'],'test-secret')
        self.assertNotIn('authorization',requests[1].headers)

    def test_failed_verification_hides_provider_response(self):
        provider = SpeechProvider(httpx.MockTransport(lambda request:httpx.Response(400,text='test-secret raw provider body')))
        result = provider.test(dict(protocol='openai_responses',base_url='https://same.test',model='chat'),'test-secret')
        self.assertEqual(result['status'],'unsupported')
        self.assertNotIn('test-secret',json.dumps(result))


class FakeSpeech:
    def __init__(self): self.calls=[]; self.hook=None; self.audio=encode_audio(WAV)
    def test(self,profile,key): return dict(status='supported',default_voice='alloy')
    def synthesize(self,profile,key,text,voice=None,**kwargs):
        self.calls.append((profile['profile_id'],key,text,voice))
        if self.hook: self.hook()
        return dict(self.audio)


class VoiceReplyTests(RoleTestCase):
    configured = media.ReplyMediaStoreTests.configured
    delta = media.ReplyMediaStoreTests.delta
    start = media.ReplyMediaStoreTests.start
    wait_run = media.ReplyMediaStoreTests.wait_run
    prepare = media.ReplyMediaStoreTests.prepare
    reply = media.ReplyMediaStoreTests.reply

    def setUp(self):
        super().setUp()
        self.speech = FakeSpeech()
        self.app.state.ai.speech.provider = self.speech

    def test_mixed_queue_snapshot_preview_and_uncertain_send_stops_tail(self):
        self.prepare()
        run = self.reply('前文\n[[SPLIT]]\n[[VOICE]]\n你好呀\n[[SPLIT]]\n后文')
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual([p['kind'] for p in run['reply_parts']],['text','voice','text'])
        self.assertEqual(run['reply_format_version'],3)
        self.assertEqual(self.speech.calls,[(self.profile['profile_id'],'synthetic-test-only','你好呀','alloy')])
        preview = self.post('ai-runs/media/query',account_id=self.ids[0],conversation_id=self.cids[0],run_id=run['run_id'],part_index=1)
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(base64.b64decode(preview.json()['data_base64']),self.speech.audio['wav'])
        wrong = self.post('ai-runs/media/query',account_id=self.ids[1],conversation_id=self.cids[1],run_id=run['run_id'],part_index=1)
        self.assertEqual(wrong.status_code,404)
        first = self.store.claim_delivery(run['run_id'],0)
        self.store.finish_delivery(first,dict(status='sent'))
        job = self.store.claim_delivery(run['run_id'],1)
        self.assertEqual(self.store.delivery_media(job)['data'],self.speech.audio['silk'])
        self.store.finish_delivery(job,dict(status='unknown',code='native_result_uncertain'))
        self.assertEqual(self.store.delivery_candidates(),[])
        self.assertEqual(self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])['delivery']['parts'][2]['status'],'queued')

    def test_voice_failure_falls_back_to_whole_original_segment_once(self):
        self.prepare()
        original = '一句较长的中文。'*40
        def fail():
            if len(self.speech.calls)==2: raise ConversationError('voice_network_error','连接失败',503)
        self.speech.hook=fail
        with patch.object(self.provider,'generate',return_value=dict(memory='新记忆',reply='[[VOICE]]\n'+original)) as generate:
            run = self.wait_run(self.start())
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(''.join(p['text'] for p in run['reply_parts']),original)
        self.assertTrue(all(p['kind']=='text' and p['fallback_reason']=='voice_network_error' for p in run['reply_parts']))
        self.assertEqual(generate.call_count,1)
        self.assertEqual(self.store.call(lambda:self.store.db.execute('SELECT count(*) FROM reply_audio').fetchone()[0]),0)

    def test_voice_receipt_requires_type_34_and_matching_archive(self):
        self.prepare()
        run=self.reply('[[VOICE]]\n归档确认')
        job=self.store.claim_delivery(run['run_id'],0)
        item=self.delta(3,type='voice',is_self=True,content={})
        created=int(datetime.fromisoformat(item['sent_at']).timestamp())
        evidence=dict(token='f'*32,local_id=item['_ref']['local_id'],create_time=created,
                      timestamp_ms=created*1000,source_uuid='12345678-1234-1234-1234-123456789abc',
                      message_type=34,target=job['target'])
        def assign():
            with self.store.db:
                self.store.db.execute('UPDATE messages SET server_id=? WHERE local_id=? AND account_id=?',
                                      ('999',item['_ref']['local_id'],self.ids[0]))
        self.store.call(assign)
        self.assertIsNone(self.store.corroborate_delivery(job,dict(evidence,message_type=3)))
        self.assertIsNone(self.store.corroborate_delivery(job,dict(evidence,target='other')))
        self.assertIsNotNone(self.store.corroborate_delivery(job,evidence))
        self.store.finish_delivery(job,dict(status='sent',evidence=evidence))
        self.assertEqual(self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])['delivery']['parts'][0]['confirmation'],'native_archive')

    def test_cancel_discards_prepared_audio_without_delivery(self):
        self.prepare()
        def cancel_second():
            if len(self.speech.calls)==2:
                self.update(self.preferences()['revision'],ai_enabled=False)
        self.speech.hook=cancel_second
        run=self.reply('[[VOICE]]\n第一段\n[[SPLIT]]\n[[VOICE]]\n第二段')
        self.assertEqual(run['status'],'cancelled',run)
        self.assertEqual(run['delivery']['total_parts'],0)
        for table in ('voice_preparations','reply_audio'):
            self.assertEqual(self.store.call(lambda:self.store.db.execute('SELECT count(*) FROM '+table).fetchone()[0]),0)

    def test_unverified_voice_is_text_without_synthesis(self):
        self.prepare()
        self.store.save_speech_verification(self.profile['profile_id'],1,dict(status='unsupported'))
        run=self.reply('[[VOICE]]\n原文保留')
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(run['reply_parts'][0]['kind'],'text')
        self.assertEqual(self.speech.calls,[])

    def test_cancel_and_revision_change_during_synthesis_never_commit(self):
        self.prepare()
        self.speech.hook=lambda:self.update(self.preferences()['revision'],memory='手工保留')
        run=self.reply('[[VOICE]]\n不能覆盖手工修改')
        self.assertEqual(run['status'],'conflicted',run)
        self.assertEqual(self.preferences()['memory'],'手工保留')
        self.assertEqual(run['delivery']['total_parts'],0)

    def test_disable_during_synthesis_never_sends(self):
        self.prepare()
        self.speech.hook=lambda:self.update(self.preferences()['revision'],ai_enabled=False)
        run=self.reply('[[VOICE]]\n不应发送')
        self.assertEqual(run['status'],'cancelled',run)
        self.assertEqual(run['delivery']['total_parts'],0)

    def test_role_voice_binding_is_used_and_preview_does_not_enqueue(self):
        self.prepare()
        role=self.store.role_card(self.preferences()['effective']['role_card_code'])
        role['card']['data']['extensions']['wechat_agent_bridge']={'voices':{self.profile['profile_id']:'nova'}}
        self.store.save_role_card(role['card'],role['card_code'],role['revision'])
        run=self.reply('[[VOICE]]\n按角色朗读')
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(self.speech.calls[-1][-1],'nova')
        count=self.store.call(lambda:self.store.db.execute('SELECT count(*) FROM deliveries').fetchone()[0])
        preview=self.post('voice/preview',profile_id=self.profile['profile_id'],expected_revision=1,voice='shimmer')
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(self.speech.calls[-1][-1],'shimmer')
        self.assertEqual(self.store.call(lambda:self.store.db.execute('SELECT count(*) FROM deliveries').fetchone()[0]),count)

    def test_restart_resumes_prepared_result_without_chat_request(self):
        self.prepare()
        with patch.object(self.app.state.ai.speech,'start_run'), patch.object(self.provider,'generate',return_value=dict(memory='保存的记忆',reply='[[VOICE]]\n保存的原文')):
            run=self.start()
            for _ in range(200):
                if self.store.voice_preparation(run['run_id']): break
                time.sleep(.01)
        self.assertIsNotNone(self.store.voice_preparation(run['run_id']))
        self.store.close()
        reopened=Store(self.path)
        worker=AiExecution(reopened,self.app.state.bridge,self.provider,False,self.speech)
        try:
            with patch.object(self.provider,'generate',side_effect=AssertionError('must not repeat chat')) as generate:
                worker.perform(self.ids[0],self.cids[0],run['run_id'])
                worker.close()
            result=reopened.ai_run(self.ids[0],self.cids[0],run['run_id'])
            self.assertEqual(result['status'],'completed',result)
            self.assertEqual(result['reply_parts'][0]['text'],'保存的原文')
            generate.assert_not_called()
            self.assertEqual(reopened.call(lambda:reopened.db.execute('PRAGMA foreign_key_check').fetchall()),[])
        finally: worker.close();reopened.close()

    def test_fallback_chat_profile_supplies_its_own_key_model_and_role_voice(self):
        self.prepare()
        backup=self.create_api(name='备用',api_key='backup-only-key',speech_model='backup-speech')
        role=self.store.role_card(self.preferences()['effective']['role_card_code'])
        role['card']['data']['extensions']['wechat_agent_bridge']={'voices':{self.profile['profile_id']:'alloy',backup['profile_id']:'nova'}}
        self.store.save_role_card(role['card'],role['card_code'],role['revision'])
        seen=[]
        def generate(profile,key,envelope,mode):
            seen.append(profile['profile_id'])
            if profile['profile_id']==self.profile['profile_id']:
                error=ConversationError('ai_provider_error','当前 Agent 不可用',503);error.retryable=False
                raise error
            self.assertTrue(envelope['reply_media']['capabilities']['voice'])
            return dict(memory='切换后的记忆',reply='[[VOICE]]\n备用角色语音')
        with patch.object(self.provider,'generate',side_effect=generate):
            run=self.store.start_ai_run(self.ids[0],self.cids[0],'voice-fallback','manual')
            self.app.state.ai.perform(self.ids[0],self.cids[0],run['run_id'])
            self.app.state.ai.perform(self.ids[0],self.cids[0],run['run_id'])
            run=self.wait_run(run)
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(seen,[self.profile['profile_id'],backup['profile_id']])
        self.assertEqual(self.speech.calls,[(backup['profile_id'],'backup-only-key','备用角色语音','nova')])
