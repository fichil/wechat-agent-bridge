"""One durable logical run, with bounded network attempts and a fixed request."""
import json
import time
from .conversations import ConversationError
from .ai_execution_storage import pack, stamp


class AiRetryStoreMixin:
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
        if not prefs['ai_enabled'] or self._ai_session(aid,cid)['epoch'] != run.get('epoch') or not self._group_run_allowed(run):
            return ('cancelled','ai_cancelled','AI 或本轮触发已关闭，旧请求已取消。')
        if run.get('snapshot_taken'):
            if prefs['memory_revision'] != run['memory_revision']:
                return ('conflicted','ai_memory_conflict','记忆已被手工修改，旧请求已停止，请重试。')
            effective = self._effective(prefs)
            if (effective['role_card_code'] != run['role_card_code'] or effective['api_profile_id'] != run['profile_id']
                    or self._card(run['role_card_code'])['revision'] != run['role_revision']
                    or self._profile(run['profile_id'])['revision'] != run['profile_revision']
                    or self.db.execute('SELECT revision FROM ai_settings').fetchone()[0] != run['settings_revision']):
                return ('conflicted','ai_configuration_changed','角色、API 或提示词已更新，旧请求已停止，请重试。')
        return None

    def _ai_terminate_invalid(self, run):
        invalid = self._ai_invalidated(run)
        if not invalid: return False
        status,code,message = invalid
        run.update(status=status,error=code,message=message,completed_at=stamp(),next_retry_at=None)
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
        secret = self.db.execute('SELECT secret FROM api_profiles WHERE profile_id=?',(run['profile_id'],)).fetchone()[0]
        run.update(status='running',next_retry_at=None)
        self._ai_write_run(run)
        return dict(value, encrypted_key=bytes(secret), resumed=True)

    def _schedule_ai_retry(self, run, error):
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
                    if run.get('next_retry_at',0) and run['next_retry_at']>time.time(): continue
                    if not self._compatibility_blocked(aid): result.append(run)
            for event in events: self.notify(event)
            return result
        return self.call(query)

    def _recover_retry(self, run):
        if 'max_retries' not in run: return False  # Preserve legacy paused/interrupted semantics.
        snapshot = self.db.execute('SELECT 1 FROM ai_request_snapshots WHERE run_id=?',(run['run_id'],)).fetchone()
        if run.get('attempt_count',0) > run['max_retries']:
            return False
        if not snapshot:
            self.db.execute('DELETE FROM ai_run_messages WHERE run_id=?',(run['run_id'],))
            self.db.execute("UPDATE ai_message_state SET status='pending' WHERE run_id=? AND status='processing'",(run['run_id'],))
            run['snapshot_taken']=False
        run.update(status='queued',next_retry_at=max(time.time(),run.get('next_retry_at') or 0))
        self._ai_write_run(run)
        return True
