"""Bounded speech work; HTTP and SILK encoding never run on the SQL thread."""
import base64
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from . import secret_protection
from .conversations import ConversationError
from .reply_media import parse_reply
from .voice_audio import split_voice_text
from .voice_provider import SpeechProvider, voice_for_card, instructions_for_card
from .voice_service import service_for_card, service_profile


class SpeechExecution:
    def __init__(self, owner, provider=None):
        self.owner, self.store = owner, owner.store
        self.provider = provider
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='speech')
        self.lock = threading.RLock()
        self.pending = set()
        self.closed = False

    def submit(self, key, work):
        with self.lock:
            if self.closed or self.owner.update_draining or key in self.pending or len(self.pending) >= 2:
                return None
            self.pending.add(key)
        def perform():
            try: return work()
            finally:
                with self.lock: self.pending.discard(key)
                self.owner.wake.set()
        return self.executor.submit(perform)

    def test(self, fields, key):
        return self.provider.test(fields, key) if self.provider else dict(status='unverified')

    def decorate(self, profile, envelope, aid):
        current = self.store.api_profile(profile['profile_id'])
        binding = service_for_card(envelope.get('character_card', {}))
        service = self.store.voice_service() if binding is not None else None
        native = (self.owner.bridge.compatibility.sender.capability(aid).get('send_voice', False)
                  if self.owner.bridge.compatibility.enabled else self.provider is not None)
        ready = (current['revision'] == profile['revision'] and self.provider is not None and native
                 and envelope.get('reply_allowed', False)
                 and (service['key_readable'] and service['verification']['status'] == 'supported'
                      if service is not None else current['verification'].get('speech', {}).get('status') == 'supported'))
        catalog = envelope.setdefault('reply_media', {})
        catalog.setdefault('capabilities', {})['voice'] = bool(ready)
        catalog['voice'] = dict(max_duration_ms=60000, ai_generated=True) if ready else None
        if ready and service is not None:
            catalog['voice'].update(service_id=service['service_id'], service_revision=service['revision'])

    def tick(self):
        for row in self.store.voice_preparation_rows():
            self.start_run(row['account_id'], row['conversation_id'], row['run_id'])
        if self.provider is None: return
        for profile in self.store.api_profiles()['items']:
            if (profile['key_readable'] and profile['verification']['status'] == 'passed'
                    and profile['verification'].get('speech', {}).get('status', 'unverified') == 'unverified'):
                def check(p=profile):
                    pid, revision = p['profile_id'], p['revision']
                    try:
                        if not self.store.save_speech_verification(pid, revision, dict(status='testing')): return
                        fields, secret = self.store.speech_credentials(pid, revision)
                        result = self.test(fields, secret_protection.reveal(secret))
                        self.store.save_speech_verification(pid, revision, result)
                    except ConversationError:
                        pass  # Deleted/revised profiles never receive the old result.
                    except Exception:
                        self.store.save_speech_verification(pid, revision, dict(status='failed', error='voice_test_failed', message='语音能力测试未完成，请重新测试。'))
                self.submit('profile:'+profile['profile_id'], check)

    def prepare(self, aid, cid, rid, result, duration):
        parts = parse_reply(result['reply'])
        if not any(p['kind'] == 'voice' for p in parts): return False
        if self.store.begin_voice_preparation(aid,cid,rid,result,parts,duration):
            self.start_run(aid,cid,rid)
        return True

    def start_run(self, aid, cid, rid):
        return self.submit('run:'+rid, lambda: self.perform(aid,cid,rid))

    def perform(self, aid, cid, rid):
        try:
            self.owner.bridge.compatibility.guard(aid)
            if not self.store.resume_voice_preparation(aid,cid,rid): return
            run, snapshot = self.store.voice_request_snapshot(aid,cid,rid)
            value = self.store.voice_preparation(rid)
            envelope = snapshot['envelope']
            allowed = envelope.get('reply_media', {}).get('capabilities', {}).get('voice') and self.provider is not None
            binding = service_for_card(envelope['character_card'])
            service_revision = (envelope.get('reply_media', {}).get('voice') or {}).get('service_revision')
            setup_error = None
            try:
                if not allowed:
                    raise ConversationError('voice_unavailable', '当前语音服务或微信未提供已验证的语音能力。', 409)
                if binding is not None:
                    profile, secret = self.store.voice_service_credentials(service_revision)
                    voice, instructions = binding['voice'], binding['instructions']
                else:
                    profile, secret = self.store.speech_credentials(run['profile_id'], run['profile_revision'])
                    voice = voice_for_card(envelope['character_card'], profile)
                    instructions = instructions_for_card(envelope['character_card'], profile)
                key = secret_protection.reveal(secret)
            except ConversationError as exc:
                setup_error = exc.code
            except Exception:
                setup_error = 'voice_generation_failed'
            deadline = time.monotonic()+90
            for index, part in enumerate(value['parts']):
                if part['kind'] != 'voice' or str(index) in value['prepared']: continue
                if not self.store.voice_preparation_valid(aid,cid,rid): return
                chunks, fallback = [], None
                try:
                    if setup_error:
                        raise ConversationError(setup_error, '语音暂不可用，保留原文。', 409)
                    texts = split_voice_text(part['text'])
                    if len(texts) > 16:
                        raise ConversationError('voice_reply_limit', '语音段数超过上限。', 422)
                    for text in texts:
                        if not self.store.voice_preparation_valid(aid,cid,rid): return
                        if binding is not None: self.store.voice_service_credentials(service_revision)
                        audio = self.provider.synthesize(profile, key, text, voice, instructions=instructions, deadline=deadline)
                        chunks.append(dict(text=text, audio=audio))
                    if binding is not None: self.store.voice_service_credentials(service_revision)
                except ConversationError as exc:
                    fallback = exc.code
                except Exception:
                    fallback = 'voice_generation_failed'
                if not self.store.record_voice_part(aid,cid,rid,index,chunks,fallback): return
            self.store.finish_ai_run(aid,cid,rid,result=value['result'],model_duration_ms=value['model_duration_ms'])
        except ConversationError as exc:
            if exc.code == 'compatibility_paused': return
            exc.retryable = False
            self.store.finish_ai_run(aid,cid,rid,error=exc)
        except Exception:
            error = ConversationError('voice_preparation_failed', '语音准备中断，任务已保留，请检查后重试。', 503)
            error.retryable = False
            self.store.finish_ai_run(aid,cid,rid,error=error)
        finally:
            try: self.store.log_application(rid, self.store.ai_run(aid,cid,rid))
            except ConversationError: pass  # Account/run may be deleted while synthesis is in flight.

    def preview(self, pid, revision, voice, instructions='', text=''):
        if self.provider is None:
            raise ConversationError('voice_unavailable', '语音服务尚未启用。', 409)
        profile, secret = self.store.speech_credentials(pid, revision)
        def work():
            sample = text.strip() or '你好，这是一条人工智能合成的音色试听。'
            audio = self.provider.synthesize(profile, secret_protection.reveal(secret), sample, voice, instructions=instructions)
            self.store.speech_credentials(pid, revision)  # Discard stale previews.
            return dict(mime_type='audio/wav', data_base64=base64.b64encode(audio['wav']).decode('ascii'), duration_ms=audio['duration_ms'])
        future = self.submit('preview:'+pid, work)
        if future is None: raise ConversationError('voice_busy', '语音服务正在处理其他任务，请稍后试听。', 409)
        return future.result()

    def configure_service(self, revision, workspace_id, key=None):
        if self.provider is None:
            raise ConversationError('voice_unavailable', '语音服务尚未启用。', 409)
        if self.store.voice_service()['revision'] != revision:
            from .role_storage import conflict
            conflict()
        if not key or not key.strip():
            _, protected = self.store.voice_service_credentials(revision)
            key = secret_protection.reveal(protected)
        else:
            key = key.strip()
            protected = secret_protection.protect(key)
        def work():
            result = self.test(service_profile(workspace_id), key)
            if result.get('status') != 'supported':
                raise ConversationError('voice_service_test_failed', '百炼语音测试未通过，配置未保存。请检查工作空间和密钥。', 422)
            return self.store.save_voice_service(revision, workspace_id, protected, result)
        future = self.submit('service:bailian', work)
        if future is None: raise ConversationError('voice_busy', '语音服务正在处理其他任务，请稍后重试。', 409)
        return future.result()

    def preview_service(self, revision, voice, instructions='', text=''):
        if self.provider is None:
            raise ConversationError('voice_unavailable', '语音服务尚未启用。', 409)
        profile, secret = self.store.voice_service_credentials(revision)
        def work():
            audio = self.provider.synthesize(profile, secret_protection.reveal(secret),
                text.strip() or '你好，这是一条人工智能合成的音色试听。', voice, instructions=instructions)
            self.store.voice_service_credentials(revision)
            return dict(mime_type='audio/wav', data_base64=base64.b64encode(audio['wav']).decode('ascii'), duration_ms=audio['duration_ms'])
        future = self.submit('service:bailian', work)
        if future is None: raise ConversationError('voice_busy', '语音服务正在处理其他任务，请稍后试听。', 409)
        return future.result()

    def close(self):
        with self.lock: self.closed = True
        self.executor.shutdown(wait=True)
