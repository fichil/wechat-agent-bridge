"""Audio snapshots and speech preparation checkpoints on the database owner thread."""
import base64
import hashlib
import json

from .ai_execution_storage import pack
from .conversations import ConversationError
from .role_storage import conflict
from .reply_media import MAX_REPLY_MEDIA, invalid


class VoiceStoreMixin:
    def _audio_put(self, value):
        identity = 'audio_'+hashlib.sha256(value['wav']+value['silk']).hexdigest()
        self.db.execute('INSERT OR IGNORE INTO reply_audio VALUES(?,?,?,?,?,?,?,?)',
            (identity, value['wav'], value['silk'], value['duration_ms'], value['sample_rate'],
             value['wav_sha256'], value['silk_sha256'], value['size_bytes']))
        return self._audio_value(identity)

    def _audio_value(self, identity, data=False):
        row = self.db.execute('SELECT * FROM reply_audio WHERE audio_id=?', (identity,)).fetchone()
        if row is None: raise ConversationError('voice_audio_not_found', '语音快照不存在。', 404)
        value = dict(audio_id=identity, mime_type='audio/wav', duration_ms=row['duration_ms'],
                     sample_rate=row['sample_rate'], sha256=row['wav_sha256'], size_bytes=len(row['wav']))
        if data: value['data_base64'] = base64.b64encode(row['wav']).decode('ascii')
        return value

    def _voice_collect(self):
        self.db.execute("DELETE FROM voice_preparations WHERE run_id IN (SELECT run_id FROM ai_runs WHERE status NOT IN ('queued','running','suspended'))")
        self.db.execute("DELETE FROM reply_audio WHERE audio_id NOT IN (SELECT audio_id FROM deliveries WHERE audio_id IS NOT NULL UNION SELECT j.value FROM voice_preparations p,json_tree(p.payload) j WHERE j.key='audio_id')")

    def _voice_recover(self):
        for row in self.db.execute('SELECT profile_id,payload FROM api_verifications').fetchall():
            value = json.loads(row['payload'])
            if value.get('speech', {}).get('status') == 'testing':
                value['speech'].update(status='failed', error='voice_test_interrupted', message='语音能力测试中断，请重新测试。')
                self.db.execute('UPDATE api_verifications SET payload=? WHERE profile_id=?', (pack(value), row['profile_id']))

    def speech_credentials(self, pid, revision):
        def read():
            profile = self._profile(pid)
            if profile['revision'] != revision: conflict()
            secret = self.db.execute('SELECT secret FROM api_profiles WHERE profile_id=?', (pid,)).fetchone()[0]
            return profile, bytes(secret)
        return self.call(read)

    def save_speech_verification(self, pid, revision, result):
        def write():
            with self.db:
                if self._profile(pid)['revision'] != revision: return False
                previous = self._verification(pid, revision)
                previous['speech'] = result
                self.db.execute('INSERT OR REPLACE INTO api_verifications VALUES(?,?,?)', (pid, revision, pack(previous)))
            self.notify(dict(type='changed', resources=['api_profiles']))
            return True
        return self.call(write)

    def voice_preparation(self, rid):
        def read():
            row = self.db.execute('SELECT payload FROM voice_preparations WHERE run_id=?', (rid,)).fetchone()
            return json.loads(row[0]) if row else None
        return self.call(read)

    def begin_voice_preparation(self, aid, cid, rid, result, parts, model_duration_ms):
        def write():
            with self.db:
                run = self._media_run(aid, cid, rid)
                if run['status'] != 'running' or self._ai_terminate_invalid(run): return False
                media_bytes, media_count = 0, 0
                for part in parts:
                    if part['kind'] not in ('image', 'sticker'): continue
                    media_count += 1
                    if 'media' in part:
                        media_bytes += part['media']['size_bytes']
                    else:
                        row = self.db.execute('SELECT m.size_bytes FROM reply_candidates c JOIN reply_media m USING(media_id) WHERE c.run_id=? AND c.kind=? AND c.reference=?',
                                              (rid,part['kind'],part['reference'])).fetchone()
                        if row is None: invalid('回复引用的素材不在本次可用清单中。')
                        media_bytes += row[0]
                if media_bytes > MAX_REPLY_MEDIA: invalid('一次回复的媒体总量不能超过 16 MiB。')
                run['reply_format_version'] = 3
                run['voice_phase'] = 'preparing'
                run.setdefault('timings_ms', {})['model_request'] = model_duration_ms
                self._ai_write_run(run)
                self.db.execute('INSERT OR IGNORE INTO voice_preparations VALUES(?,?)',
                    (rid, pack(dict(result=result, parts=[{'kind':p['kind'], 'text':p.get('text','')} for p in parts],
                                    prepared={}, media_bytes=media_bytes, media_count=media_count, model_duration_ms=model_duration_ms))))
                event = self._ai_touch(aid,cid,[])
            self.notify(event)
            return True
        return self.call(write)

    def voice_preparation_valid(self, aid, cid, rid):
        def check():
            with self.db:
                run = self._media_run(aid,cid,rid)
                return run['status'] == 'running' and not self._ai_terminate_invalid(run)
        return self.call(check)

    def record_voice_part(self, aid, cid, rid, index, chunks=None, fallback=None):
        def write():
            nonlocal fallback
            with self.db:
                run = self._media_run(aid,cid,rid)
                if run['status'] != 'running' or self._ai_terminate_invalid(run): return False
                row = self.db.execute('SELECT payload FROM voice_preparations WHERE run_id=?', (rid,)).fetchone()
                if row is None: return False
                value = json.loads(row[0])
                if str(index) in value['prepared']: return True
                if not fallback:
                    additional = sum(c['audio']['size_bytes'] for c in chunks)
                    if value['media_bytes']+additional > MAX_REPLY_MEDIA or value['media_count']+len(chunks) > 16:
                        fallback = 'voice_reply_limit'
                    else:
                        value['media_bytes'] += additional
                        value['media_count'] += len(chunks)
                value['prepared'][str(index)] = {'fallback_reason': fallback} if fallback else {
                    'chunks': [dict(text=c['text'], audio=self._audio_put(c['audio'])) for c in chunks]}
                self.db.execute('UPDATE voice_preparations SET payload=? WHERE run_id=?', (pack(value), rid))
                return True
        return self.call(write)

    def voice_preparation_rows(self):
        return self.call(lambda: [dict(r) for r in self.db.execute("SELECT a.run_id,a.account_id,a.conversation_id FROM voice_preparations v JOIN ai_runs a USING(run_id) JOIN conversation_preferences p USING(account_id,conversation_id) WHERE a.status IN ('queued','running') AND p.ai_enabled=1")])

    def resume_voice_preparation(self, aid, cid, rid):
        def write():
            with self.db:
                run = self._media_run(aid,cid,rid)
                if run['status'] not in ('queued','running') or self._ai_terminate_invalid(run): return False
                if self._compatibility_blocked(aid): return False
                run.update(status='running',next_retry_at=None)
                self._ai_write_run(run)
                return True
        return self.call(write)

    def voice_request_snapshot(self, aid, cid, rid):
        def read():
            run = self._media_run(aid,cid,rid)
            row = self.db.execute('SELECT payload FROM ai_request_snapshots WHERE run_id=?', (rid,)).fetchone()
            if row is None: raise ConversationError('voice_preparation_missing', '语音任务的请求快照不存在。', 409)
            return run, json.loads(row[0])
        return self.call(read)

    def save_voice_request_capability(self, rid, envelope):
        def write():
            with self.db:
                row = self.db.execute('SELECT payload FROM ai_runs WHERE run_id=?', (rid,)).fetchone()
                run = json.loads(row[0])
                if run['status'] != 'running' or self._ai_terminate_invalid(run): return False
                catalog = envelope.get('reply_media', {})
                if catalog.get('capabilities', {}).get('voice'):
                    run['reply_format_version'] = 3
                    self._ai_write_run(run)
                snapshot = self.db.execute('SELECT payload FROM ai_request_snapshots WHERE run_id=?', (rid,)).fetchone()
                if snapshot:
                    value = json.loads(snapshot[0])
                    existing = value['envelope'].setdefault('reply_media', {})
                    existing.setdefault('capabilities', {})['voice'] = catalog.get('capabilities', {}).get('voice', False)
                    existing['voice'] = catalog.get('voice')
                    self.db.execute('UPDATE ai_request_snapshots SET payload=? WHERE run_id=?', (pack(value), rid))
                return True
        return self.call(write)
