import json
import struct
import time
import unittest

import httpx

from backend.app.conversations import ConversationError
from backend.app.voice_audio import make_wav
from backend.app.voice_cosyvoice import normalize_provider_wav
from backend.app.voice_provider import SpeechProvider


PROFILE = dict(protocol='openai_responses', speech_protocol='dashscope_cosyvoice',
               base_url='https://workspace.cn-beijing.maas.aliyuncs.com/api/v1',
               model='chat', speech_model='cosyvoice-v3-flash')
RESULT_URL = 'http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/pre/result.wav?signature=private'
WAV = make_wav(b'\0\0'*2400)


class CosyVoiceTests(unittest.TestCase):
    def test_child_voice_instruction_and_text_survive_and_download_has_no_credential(self):
        requests = []
        def handle(request):
            requests.append(request)
            if request.method == 'POST':
                return httpx.Response(200, json=dict(output=dict(finish_reason='stop', audio=dict(url=RESULT_URL))))
            return httpx.Response(200, content=WAV)
        text = '才不是大肥鱼。\n我听着。'
        instruction = '你说话的角色是傲娇公主，你说话的情感是neutral。'
        result = SpeechProvider(httpx.MockTransport(handle)).synthesize(PROFILE, 'test-secret', text,
                  'longhuhu_v3', instructions=instruction)
        self.assertEqual(result['duration_ms'], 100)
        self.assertEqual(requests[0].headers['authorization'], 'Bearer test-secret')
        self.assertEqual(json.loads(requests[0].content)['input'], dict(text=text, voice='longhuhu_v3',
                         format='wav', sample_rate=24000, instruction=instruction))
        self.assertEqual(requests[1].url.scheme, 'https')
        self.assertNotIn('authorization', requests[1].headers)

    def test_untrusted_result_urls_never_receive_a_download(self):
        for url in ('https://127.0.0.1/a', 'https://evil.test/a', RESULT_URL.replace('.com/', '.com.evil.test/'),
                    RESULT_URL.replace('http://', 'http://user:pass@'), RESULT_URL+'#fragment'):
            requests = []
            def handle(request):
                requests.append(request)
                return httpx.Response(200, json=dict(output=dict(finish_reason='stop', audio=dict(url=url))))
            with self.subTest(url=url), self.assertRaises(ConversationError) as caught:
                SpeechProvider(httpx.MockTransport(handle)).synthesize(PROFILE, 'test-secret', '你好')
            self.assertEqual(caught.exception.code, 'voice_audio_invalid')
            self.assertEqual(len(requests), 1)

    def test_limits_and_expired_budget_stop_before_network(self):
        def unexpected(_request): raise AssertionError('must not send')
        provider = SpeechProvider(httpx.MockTransport(unexpected))
        for options in (dict(instructions='童'*51), dict(deadline=time.monotonic()-1)):
            with self.assertRaises(ConversationError): provider.synthesize(PROFILE, 'test-secret', '你好', **options)
        for base in ('http://workspace.cn-beijing.maas.aliyuncs.com/api/v1', 'https://evil.test/api/v1'):
            with self.assertRaises(ConversationError): provider.synthesize(dict(PROFILE,base_url=base), 'test-secret', '你好')

    def test_non_streaming_result_may_omit_stream_finish_reason(self):
        def handle(request):
            if request.method == 'POST':
                return httpx.Response(200,json=dict(output=dict(audio=dict(url=RESULT_URL))))
            return httpx.Response(200,content=WAV)
        self.assertEqual(SpeechProvider(httpx.MockTransport(handle)).synthesize(PROFILE,'test-secret','你好')['duration_ms'],100)

    def test_observed_streaming_placeholder_is_repaired_without_relaxing_other_wav_checks(self):
        streaming = bytearray(WAV)
        struct.pack_into('<I',streaming,4,0x7fffffbf)
        struct.pack_into('<I',streaming,40,0x7fffff9b)
        self.assertEqual(normalize_provider_wav(bytes(streaming)),WAV)
        broken = bytearray(WAV)
        struct.pack_into('<I',broken,4,len(WAV)+100)
        self.assertEqual(normalize_provider_wav(bytes(broken)),bytes(broken))

    def test_failed_or_incomplete_requests_are_not_retried_and_hide_provider_body(self):
        for status, body in ((429, {'message':'test-secret'}), (200, {'output':{'finish_reason':None}})):
            requests = []
            def handle(request):
                requests.append(request)
                return httpx.Response(status, json=body)
            result = SpeechProvider(httpx.MockTransport(handle)).test(PROFILE,'test-secret')
            self.assertEqual(result['status'],'failed')
            self.assertEqual(len(requests),1)
            self.assertNotIn('test-secret',json.dumps(result))
