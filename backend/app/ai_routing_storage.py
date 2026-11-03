"""Per-run Agent routing; all helpers run on the SQL owner thread."""
import json
import time

from .ai_execution_storage import pack, stamp
from .conversations import ConversationError


FAILOVER_ERRORS = {
    'ai_network_error', 'ai_provider_error', 'ai_response_invalid', 'ai_empty_reply', 'reply_invalid',
    'ai_format_unsupported', 'ai_vision_unsupported', 'ai_input_too_large',
    'api_key_unreadable', 'api_profile_unavailable',
}


class AiRoutingStoreMixin:
    def _route_configuration(self, prefs):
        pid = prefs['api_profile_id'] or self._defaults()['api_profile_id']
        row = self.db.execute('SELECT revision FROM api_profiles WHERE profile_id=?', (pid,)).fetchone()
        return dict(profile_id=pid, revision=row[0] if row else None,
                    source='explicit' if prefs['api_profile_id'] else 'default')

    def _routing_profiles(self, prefs, vision=False):
        configured = self._route_configuration(prefs)
        if not configured['profile_id']:
            return []
        profiles = [self._profile(r[0]) for r in self.db.execute(
            'SELECT profile_id FROM api_profiles ORDER BY created_at,profile_id').fetchall()]
        index = next((i for i, p in enumerate(profiles) if p['profile_id'] == configured['profile_id']), 0)
        return [p for p in profiles[index:] + profiles[:index]
                if p['key_readable'] and p['verification']['status'] == 'passed'
                and (not vision or p['verification'].get('vision') == 'supported')]

    def _route_requires_vision(self, aid, cid):
        return any(self._ai_item(row, True)[3]['status'] in ('pending', 'failed')
                   for row in self.db.execute(
                       "SELECT m.* FROM messages m JOIN media_cache c ON c.cache_key='image:'||m.message_id "
                       "AND c.account_id=m.account_id WHERE m.account_id=? AND m.conversation_id=? "
                       "AND json_extract(m.payload,'$.type') IN ('image','sticker')", (aid, cid)))

    def _route_initialize(self, run, prefs, profiles):
        run.update(route_configuration=self._route_configuration(prefs),
                   route_candidates=[dict(profile_id=p['profile_id'], revision=p['revision']) for p in profiles],
                   route_tried=[], route_errors={}, retry_count=0, profile_attempts=[])

    def _route_attempt_result(self, run, status, error=None):
        attempts = run.get('profile_attempts', [])
        if attempts and attempts[-1]['status'] == 'running':
            attempts[-1].update(status=status, completed_at=stamp(), error=error)

    def _route_candidate(self, candidate, vision):
        row = self.db.execute('SELECT revision FROM api_profiles WHERE profile_id=?', (candidate['profile_id'],)).fetchone()
        if not row or row[0] != candidate['revision']:
            return None
        profile = self._profile(candidate['profile_id'])
        if (not profile['key_readable'] or profile['verification']['status'] != 'passed' or
                (vision and profile['verification'].get('vision') != 'supported')):
            return None
        return profile

    def _schedule_route_retry(self, run, error):
        """Try untouched candidates immediately, then share a finite retry budget."""
        self._route_attempt_result(run, 'failed', error.code)
        row = self.db.execute('SELECT payload FROM ai_request_snapshots WHERE run_id=?', (run['run_id'],)).fetchone()
        if error.code not in FAILOVER_ERRORS or not row:
            return False
        snapshot = json.loads(row[0])
        vision = any(m['content'].get('image') for m in snapshot['envelope']['messages'])
        pid = run['profile_id']
        if pid not in run['route_tried']:
            run['route_tried'].append(pid)
        retryable = getattr(error, 'retryable', error.code in ('ai_network_error', 'ai_response_invalid', 'ai_empty_reply'))
        run['route_errors'][pid] = dict(retryable=retryable,
                                       retry_at=time.time() + (getattr(error, 'retry_after', 0) or 0))
        candidates = run['route_candidates']
        index = next((i for i, p in enumerate(candidates) if p['profile_id'] == pid), -1)
        ordered = candidates[index + 1:] + candidates[:index + 1]
        available = [p for c in ordered if (p := self._route_candidate(c, vision)) is not None]
        chosen = next((p for p in available if p['profile_id'] not in run['route_tried']), None)
        delay = 0
        if chosen is None:
            if run['retry_count'] >= run['max_retries']:
                return False
            retryable_profiles = [p for p in available if run['route_errors'].get(p['profile_id'], {}).get('retryable')]
            if not retryable_profiles:
                return False
            # Respect each provider's Retry-After without holding a model worker.
            chosen = min(retryable_profiles, key=lambda p: run['route_errors'][p['profile_id']]['retry_at'])
            delay = max(min(30, 2 ** min(run['retry_count'] + 1, 5)),
                        run['route_errors'][chosen['profile_id']]['retry_at'] - time.time())
        snapshot['profile'] = chosen
        self.db.execute('UPDATE ai_request_snapshots SET payload=? WHERE run_id=?', (pack(snapshot), run['run_id']))
        run.update(status='queued', profile_id=chosen['profile_id'], profile_revision=chosen['revision'],
                   profile_name=chosen['name'], next_retry_at=time.time() + delay, error=error.code,
                   message=(f"等待重试 Agent：{chosen['name']}（已额外重试 {run['retry_count']}/{run['max_retries']} 次）。"
                            if delay else f"正在切换到下一条 Agent：{chosen['name']}。"))
        self._ai_write_run(run)
        return True

    def _recover_route(self, run, snapshot):
        if (snapshot and run['status'] == 'running' and run.get('profile_attempts')
                and run['profile_attempts'][-1]['status'] == 'running'):
            self._route_attempt_result(run, 'interrupted', 'ai_interrupted')
            # An interrupted HTTP request has consumed its attempt, even after restart.
            error = ConversationError('ai_network_error', '服务重启，恢复同一批消息。')
            if not self._schedule_route_retry(run, error):
                return False
        if not snapshot:
            self.db.execute('DELETE FROM ai_run_messages WHERE run_id=?', (run['run_id'],))
            self.db.execute("UPDATE ai_message_state SET status='pending' WHERE run_id=? AND status='processing'", (run['run_id'],))
            run['snapshot_taken'] = False
        run.update(status='queued', next_retry_at=max(time.time(), run.get('next_retry_at') or 0))
        self._ai_write_run(run)
        return True
