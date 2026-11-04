"""One durable logical run, with bounded network attempts and a fixed request."""
import json
import time
from .conversations import ConversationError
from .ai_execution_storage import pack, stamp


class AiRetryStoreMixin:
    def _suspend_background(self, run):
        aid, cid = run['account_id'], run['conversation_id']
        if (run['status'] != 'queued' or not run.get('round_id')
                or run.get('processing_type') == 'realtime_reply' or not self._group_reply_rows(aid, cid)):
            return False
        self._group_silence_round(aid, cid)
        run.update(status='suspended', processing_type='memory_backfill',
                   message='后台记忆等待实时回复完成后继续。')
        self._ai_write_run(run)
        self.db.execute("UPDATE ai_message_state SET status='pending' WHERE run_id=? AND status='processing'", (run['run_id'],))
        self.db.execute('UPDATE ai_sessions SET auto_pending=1 WHERE account_id=? AND conversation_id=?', (aid, cid))
        return True

    def _resume_background(self, aid, cid):
        row = self.db.execute("SELECT payload FROM ai_runs WHERE account_id=? AND conversation_id=? AND status='suspended' ORDER BY rowid LIMIT 1", (aid, cid)).fetchone()
        if not row:
            return None
        run = json.loads(row[0])
        if self._ai_terminate_invalid(run):
            return run
        if run.get('next_retry_at') and run['next_retry_at'] > time.time():
            return run
        # No request is in flight. Rebuild only the unprocessed snapshot members
        # with the current memory, keeping the original routing/retry budget.
        self.db.execute('DELETE FROM ai_request_snapshots WHERE run_id=?', (run['run_id'],))
        self.db.execute('DELETE FROM ai_run_messages WHERE run_id=?', (run['run_id'],))
        self.db.execute('DELETE FROM reply_candidates WHERE run_id=?', (run['run_id'],))
        rebuild = bool(run.get('snapshot_taken') or run.get('route_candidates'))
        run.update(status='queued', snapshot_taken=False, resume_background=rebuild)
        self._ai_write_run(run)
        return run

    def _advance_suspended_memory(self, aid, cid, previous, current):
        # Only our successful serialized commits can rebase a suspended job.
        # A manual edit still produces the normal memory revision conflict.
        for row in self.db.execute("SELECT payload FROM ai_runs WHERE account_id=? AND conversation_id=? AND status='suspended'", (aid, cid)).fetchall():
            run = json.loads(row[0])
            if run['memory_revision'] == previous:
                run['memory_revision'] = current
                if not self._group_round(aid, cid):
                    run.update(status='empty', completed_at=stamp(), message='本轮剩余消息已由实时回复处理完成。')
                self._ai_write_run(run)

    def defer_ai_run(self, rid):
        def write():
            with self.db:
                row=self.db.execute("SELECT payload FROM ai_runs WHERE run_id=? AND status='running'",(rid,)).fetchone()
                if row:
                    run=json.loads(row[0]); run.update(status='queued',next_retry_at=time.time()+3)
                    self._ai_write_run(run)
        self.call(write)

    def _ai_invalidated(self, run):
        aid,cid = run['account_id'],run['conversation_id']
        prefs = self._preferences(aid,cid)
        if not prefs['ai_enabled']:
            return ('cancelled','ai_cancelled','会话 AI 已关闭，旧请求已取消。')
        if self._ai_session(aid,cid)['epoch'] != run.get('epoch'):
            return ('cancelled','ai_cancelled','账号身份或会话处理代次已变化，旧请求已取消。')
        if not self._group_run_allowed(run):
            return ('cancelled','ai_cancelled','本轮群聊触发已失效，旧请求已取消。')
        if run.get('snapshot_taken') or run.get('resume_background'):
            if prefs['memory_revision'] != run['memory_revision']:
                return ('conflicted','ai_memory_conflict','记忆已被手工修改，旧请求已停止，请重试。')
            effective = self._effective(prefs)
            profile = self.db.execute('SELECT revision FROM api_profiles WHERE profile_id=?', (run['profile_id'],)).fetchone()
            configuration_changed = (self._route_configuration(prefs) != run['route_configuration']
                                     if 'route_configuration' in run else effective['api_profile_id'] != run['profile_id'])
            if (effective['role_card_code'] != run['role_card_code'] or configuration_changed
                    or self._card(run['role_card_code'])['revision'] != run['role_revision']
                    or not profile or profile[0] != run['profile_revision']
                    or self.db.execute('SELECT revision FROM ai_settings').fetchone()[0] != run['settings_revision']):
                return ('conflicted','ai_configuration_changed','角色、API 或提示词已更新，旧请求已停止，请重试。')
        return None

    def _ai_terminate_invalid(self, run):
        invalid = self._ai_invalidated(run)
        if not invalid: return False
        status,code,message = invalid
        run.update(status=status,error=code,message=message,completed_at=stamp(),next_retry_at=None)
        self._route_attempt_result(run, 'interrupted', code)
        self._ai_write_run(run)
        self.db.execute('DELETE FROM ai_request_snapshots WHERE run_id=?',(run['run_id'],))
        self.db.execute("UPDATE ai_message_state SET status=? WHERE run_id=? AND status='processing'", ('pending' if status=='cancelled' else 'failed',run['run_id']))
        self.db.execute('UPDATE ai_sessions SET paused=?,auto_pending=0 WHERE account_id=? AND conversation_id=?', (int(status=='conflicted'),run['account_id'],run['conversation_id']))
        return True

    def save_ai_snapshot(self, aid, cid, rid, profile, envelope):
        def write():
            with self.db:
                row = self.db.execute('SELECT payload FROM ai_runs WHERE run_id=?',(rid,)).fetchone()
                if not row: return False
                run = json.loads(row[0])
                if run['status'] != 'running' or self._ai_terminate_invalid(run): return False
                self.db.execute('INSERT OR IGNORE INTO ai_request_snapshots VALUES(?,?)', (rid,pack(dict(profile=profile,envelope=envelope))))
                return True
        return self.call(write)

    def _ai_retry_snapshot(self, run):
        row = self.db.execute('SELECT payload FROM ai_request_snapshots WHERE run_id=?',(run['run_id'],)).fetchone()
        if not row: return None
        value = json.loads(row[0])
        if run.get('round_id') and 'reply_required' not in value['envelope']:
            value['envelope']['reply_required'] = self._ai_reply_envelope(run)['reply_required']
        if 'route_candidates' in run:
            vision = any(m['content'].get('image') for m in value['envelope']['messages'])
            if self._route_candidate(dict(profile_id=run['profile_id'], revision=run['profile_revision']), vision) is None:
                raise ConversationError('api_profile_unavailable', '候选 Agent 已不可用，尝试下一条配置。', 409)
        secret = self.db.execute('SELECT secret FROM api_profiles WHERE profile_id=?',(run['profile_id'],)).fetchone()[0]
        run.update(status='running',next_retry_at=None)
        self._ai_write_run(run)
        return dict(value, encrypted_key=bytes(secret), resumed=True)

    def _schedule_ai_retry(self, run, error):
        if 'route_candidates' in run:
            return self._schedule_route_retry(run, error)
        retryable = getattr(error,'retryable',error.code in ('ai_network_error','ai_response_invalid'))
        if (not retryable or not run.get('attempt_count') or run['attempt_count'] > run.get('max_retries',0)
                or not self.db.execute('SELECT 1 FROM ai_request_snapshots WHERE run_id=?',(run['run_id'],)).fetchone()): return False
        delay = max(min(30,2**min(run['attempt_count'],5)),getattr(error,'retry_after',0) or 0)
        run.update(status='queued',next_retry_at=time.time()+delay,error=error.code,
                   message=f"第 {run['attempt_count']} 次请求失败，等待自动重试（最多额外 {run['max_retries']} 次）。")
        self._ai_write_run(run)
        return True

    def ai_retry_candidates(self):
        def query():
            events, result = [], []
            with self.db:
                for row in self.db.execute("SELECT payload FROM ai_runs WHERE status='queued'").fetchall():
                    run=json.loads(row[0]); aid,cid=run['account_id'],run['conversation_id']
                    if self._ai_terminate_invalid(run):
                        events.append(self._ai_touch(aid,cid,[])); continue
                    if self._suspend_background(run):
                        events.append(self._ai_touch(aid,cid,[])); continue
                    if run.get('next_retry_at',0) and run['next_retry_at']>time.time(): continue
                    if not self._compatibility_blocked(aid): result.append(run)
            for event in events: self.notify(event)
            return result
        return self.call(query)

    def _recover_retry(self, run):
        from .delivery_storage import REPLY_FORMAT_VERSION
        if run.get('reply_format_version', 1) > REPLY_FORMAT_VERSION: return False
        if 'max_retries' not in run: return False  # Preserve legacy paused/interrupted semantics.
        snapshot = self.db.execute('SELECT 1 FROM ai_request_snapshots WHERE run_id=?',(run['run_id'],)).fetchone()
        if 'route_candidates' in run:
            return self._recover_route(run, snapshot)
        if run.get('attempt_count',0) > run['max_retries']:
            return False
        if not snapshot:
            self.db.execute('DELETE FROM ai_run_messages WHERE run_id=?',(run['run_id'],))
            self.db.execute("UPDATE ai_message_state SET status='pending' WHERE run_id=? AND status='processing'",(run['run_id'],))
            run['snapshot_taken']=False
        run.update(status='queued',next_retry_at=max(time.time(),run.get('next_retry_at') or 0))
        self._ai_write_run(run)
        return True
