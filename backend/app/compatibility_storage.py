"""Durable compatibility holds, independent of conversation/user pause switches."""
from datetime import datetime, timezone
import json
import time

from .conversations import ConversationError

COMPATIBILITY_SCHEMA = (
    'CREATE TABLE compatibility_state(scope TEXT PRIMARY KEY, revision INTEGER NOT NULL, fingerprint TEXT NOT NULL, held INTEGER NOT NULL, reason TEXT NOT NULL, snapshot TEXT NOT NULL)',
)
TRANSIENT_SEND_HOLDS = frozenset(('native_busy', 'sync_pending', 'sync_error', 'native_unavailable'))
LOGIN_RECOVERY_HOLDS = frozenset(('source_offline', 'unidentified_client', 'probe_failed', 'process_changed'))


def upgrade_v9(db):
    if db.execute('PRAGMA user_version').fetchone()[0] == 8:
        with db:
            db.execute('BEGIN IMMEDIATE')
            for statement in COMPATIBILITY_SCHEMA:
                db.execute(statement)
            db.execute('INSERT INTO schema_migrations VALUES(9,?)', (datetime.now(timezone.utc).isoformat(),))
            db.execute('PRAGMA user_version=9')


class CompatibilityStoreMixin:
    compatibility_enforced = False
    migration_hold = False  # legacy test fixtures only
    migration_token = None
    deployment_hold = False

    def migration_pending_for(self, aid):
        snapshot = json.loads(self._compatibility_row(aid)["snapshot"])
        return bool(self.migration_token and snapshot.get("migration_token") != self.migration_token)

    def configuration_ready(self, aid, cid):
        return self._effective(self._preferences(aid, cid))["ready"]

    def _compatibility_row(self, scope):
        row = self.db.execute('SELECT * FROM compatibility_state WHERE scope=?', (scope,)).fetchone()
        return dict(row) if row else dict(scope=scope, revision=0, fingerprint='', held=1, reason='not_checked', snapshot='{}')

    def compatibility_rows(self):
        return self.call(lambda: [dict(r) for r in self.db.execute('SELECT * FROM compatibility_state ORDER BY scope')])

    def _compatibility_fence(self, aid):
        self._account(aid)
        row = self._compatibility_row(aid)
        return (self.account_generation, self.source_generation, self._compatibility_row('*')['revision'],
                row['revision'], row['fingerprint'])

    def compatibility_fence(self, aid):
        return self.call(self._compatibility_fence, aid)

    def _compatibility_check_fence(self, aid, expected):
        if expected is not None and self._compatibility_fence(aid) != expected:
            raise ConversationError('revision_conflict', '微信身份或检查状态已变化，将自动重新检查。', 409)

    def _compatibility_blocked(self, aid):
        return self.deployment_hold or self.migration_hold or self.migration_pending_for(aid) or (self.compatibility_enforced and (
            self._compatibility_row('*')['held'] or self._compatibility_row(aid)['held']))

    def compatibility_blocked(self, aid):
        return self.call(self._compatibility_blocked, aid)

    def _compatibility_capture_allowed(self, aid):
        if not self._compatibility_blocked(aid):
            return True
        # Transient readiness holds stop execution, not capture of live messages
        # or atomic acceptance of an already completed model result.
        # Identity/build/migration holds still discard eligibility until a new baseline.
        if self.deployment_hold or self.migration_hold or self.migration_pending_for(aid) or self._compatibility_row('*')['held']:
            return False
        state = self._compatibility_row(aid)
        snapshot = json.loads(state['snapshot'])
        return bool(state['fingerprint'] and snapshot.get('readable') and snapshot.get('needs_baseline') is False
                    and state['reason'] in TRANSIENT_SEND_HOLDS)

    def _compatibility_require(self, aid):
        if self._compatibility_blocked(aid):
            raise ConversationError('compatibility_paused', '自动检查尚未通过，条件恢复后将自动继续。', 409)

    def _compatibility_defer_triggers(self, aid):
        """Keep verified live arrivals while the same binding is being recovered.

        This only permits recording eligibility. Models and native sends retain
        the full compatibility gate, and a changed fingerprint discards it.
        """
        state = self._compatibility_row(aid)
        snapshot = json.loads(state['snapshot'])
        return bool(not self.deployment_hold and not self.migration_hold and not self.migration_pending_for(aid)
                    and snapshot.get('reply_resume', {}).get('fingerprint') == state['fingerprint']
                    and state['fingerprint'] and snapshot.get('readable')
                    and self._account(aid)['source_state'] == 'online'
                    and state['reason'] in LOGIN_RECOVERY_HOLDS | TRANSIENT_SEND_HOLDS)

    def _compatibility_write(self, scope, fingerprint, held, reason, snapshot):
        if scope != '*' and not self.db.execute('SELECT 1 FROM accounts WHERE account_id=?',(scope,)).fetchone(): return 0
        old = self._compatibility_row(scope)
        encoded = json.dumps(snapshot, sort_keys=True, separators=(',', ':'))
        value = (fingerprint, int(held), reason, encoded)
        if value == (old['fingerprint'], old['held'], old['reason'], old['snapshot']):
            return old['revision']
        revision = old['revision']+1
        self.db.execute('INSERT OR REPLACE INTO compatibility_state VALUES(?,?,?,?,?,?)',
                        (scope, revision, *value))
        if (old['held'], old['reason'], old['fingerprint']) != (int(held), reason, fingerprint):
            observed = getattr(self, '_compatibility_changed_at', {})
            current = time.monotonic()
            duration = int((current-observed[scope])*1000) if scope in observed else None
            observed[scope] = current
            self._compatibility_changed_at = observed
            # This runs on the SQL owner: never call log_write (which queues SQL)
            # recursively, and never let diagnostics roll back the state change.
            self.db.execute('SAVEPOINT compatibility_diagnostic')
            try:
                self._log_put(dict(category='source', operation='compatibility',
                    account_id=None if scope == '*' else scope,
                    status='completed', source='runtime', duration_ms=duration),
                    dict(previous=dict(held=bool(old['held']), reason=old['reason']),
                         current=dict(held=bool(held), reason=reason),
                         identity_changed=old['fingerprint'] != fingerprint))
            except Exception:
                self.db.execute('ROLLBACK TO compatibility_diagnostic')
                self.log_warning = '兼容状态诊断未能保存，状态检查仍有效。'
            finally:
                self.db.execute('RELEASE compatibility_diagnostic')
        return revision

    def _compatibility_invalidate(self, aid, *, recover_login=False):
        """Invalidate eligibility, never change a submitted send into retryable work."""
        old = self._compatibility_row(aid)
        snapshot = json.loads(old["snapshot"])
        if recover_login and old['fingerprint'] and snapshot.get('readable') and not snapshot.get('needs_baseline', True):
            snapshot['reply_resume'] = dict(fingerprint=old['fingerprint'], epochs={r[0]:r[1] for r in self.db.execute(
                'SELECT conversation_id,epoch FROM ai_sessions WHERE account_id=?', (aid,))})
        elif not recover_login:
            snapshot.pop('reply_resume', None)
        snapshot["needs_baseline"] = True
        self._compatibility_write(aid, old["fingerprint"], True, old["reason"], snapshot)
        timestamp = datetime.now(timezone.utc).isoformat()
        self.db.execute('UPDATE ai_sessions SET epoch=epoch+1,auto_pending=0 WHERE account_id=?', (aid,))
        self.db.execute("UPDATE deliveries SET status='cancelled',code='compatibility_changed' WHERE account_id=? AND status IN ('queued','failed')", (aid,))
        for row in self.db.execute("SELECT run_id,payload FROM ai_runs WHERE account_id=? AND status='queued'", (aid,)).fetchall():
            value = json.loads(row['payload'])
            value.update(status='cancelled', completed_at=timestamp, message='微信兼容状态变化，旧任务已取消。')
            self._ai_write_run(value)
        for row in self.db.execute("SELECT round_id,payload FROM group_rounds WHERE account_id=? AND status='active'", (aid,)).fetchall():
            value = json.loads(row['payload']); value['status'] = 'cancelled'
            self.db.execute("UPDATE group_rounds SET status='cancelled',payload=? WHERE round_id=?", (json.dumps(value), row['round_id']))

    def compatibility_start(self):
        self.compatibility_enforced = True
        def write():
            with self.db:
                self._compatibility_write('*', '', True, 'checking', {})
        self.call(write)

    def compatibility_hold(self, reason, evidence=None):
        if not self.compatibility_enforced:
            return
        def write():
            with self.db:
                self._compatibility_write('*', '', True, reason, evidence or {})
                for row in self.db.execute('SELECT account_id FROM accounts').fetchall():
                    aid = row[0]; old = self._compatibility_row(aid)
                    if not old['held']:
                        self._compatibility_invalidate(aid, recover_login=reason in LOGIN_RECOVERY_HOLDS)
                    self._compatibility_write(aid, old['fingerprint'], True, reason, json.loads(self._compatibility_row(aid)['snapshot']))
            self.notify(dict(type='changed', resources=['compatibility', 'ai_runs']))
        self.call(write)

    def compatibility_pause(self, aid, reason, *, expected=None):
        def write():
            with self.db:
                self._compatibility_check_fence(aid, expected)
                old = self._compatibility_row(aid)
                if not old['held']:
                    self._compatibility_invalidate(aid)
                self._compatibility_write(aid, old['fingerprint'], True, reason, json.loads(self._compatibility_row(aid)['snapshot']))
            self.notify(dict(type='changed', account_id=aid, resources=['compatibility','ai_runs']))
        self.call(write)

    def compatibility_observe(self, states, unresolved, *, generation=None):
        if not self.compatibility_enforced:
            return
        def write():
            if generation is not None and generation != self.account_generation:
                raise ConversationError('account_scan_obsolete', '账号已删除，请重新识别。', 409)
            with self.db:
                self._compatibility_write('*', '', bool(unresolved), 'unidentified_client' if unresolved else '', dict(instances=unresolved))
                for aid, value in states.items():
                    old = self._compatibility_row(aid)
                    changed = old['fingerprint'] != value['fingerprint']
                    value = {**json.loads(old['snapshot']), **value}
                    previous_send = json.loads(old['snapshot']).get('send')
                    if not changed and previous_send is not None:
                        value['send'] = previous_send
                    held = bool(old['held'] or changed or unresolved or not value['readable'])
                    reason = ('unidentified_client' if unresolved else 'unsupported_build' if not value['readable'] else
                              'build_changed' if changed else old['reason'] if held else '')
                    if changed or (held and not old['held']):
                        self._compatibility_invalidate(aid, recover_login=not changed and bool(unresolved))
                        recovery = json.loads(self._compatibility_row(aid)['snapshot']).get('reply_resume')
                        value.pop('reply_resume', None)
                        if recovery: value['reply_resume'] = recovery
                        value['needs_baseline'] = True
                    self._compatibility_write(aid, value['fingerprint'], held, reason, value)
                for row in self.db.execute('SELECT account_id FROM accounts').fetchall():
                    aid = row[0]
                    if aid not in states:
                        old = self._compatibility_row(aid)
                        if not old['held']:
                            self._compatibility_invalidate(aid, recover_login=True)
                        self._compatibility_write(aid, old['fingerprint'], True,
                                                  'unidentified_client' if unresolved else 'source_offline', json.loads(self._compatibility_row(aid)['snapshot']))
            self.notify(dict(type='changed', resources=['compatibility', 'ai_runs']))
        self.call(write)

    def compatibility_send_state(self, aid, ready, code, *, expected=None):
        def write():
            with self.db:
                self._compatibility_check_fence(aid, expected)
                old = self._compatibility_row(aid)
                snapshot = json.loads(old['snapshot'])
                snapshot['send'] = dict(ready=bool(ready), code=code)
                self._compatibility_write(aid, old['fingerprint'], bool(old['held'] or not ready),
                                          code if not ready else old['reason'], snapshot)
                result = self._compatibility_row(aid)
                if expected is not None: result['_fence'] = self._compatibility_fence(aid)
                return result
        return self.call(write)

    def compatibility_resume(self, aid, expected_revision, *, expected=None):
        """Internal compare-and-swap after automatic live checks; no public resume action."""
        def write():
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                self._compatibility_check_fence(aid, expected)
                state = self._compatibility_row(aid)
                if state['revision'] != expected_revision:
                    raise ConversationError('revision_conflict', '兼容状态已变化，将自动重新检查。', 409)
                if self._compatibility_row('*')['held'] or self.deployment_hold or self.migration_hold:
                    raise ConversationError('compatibility_paused', '等待自动检查。', 409)
                snapshot = json.loads(state['snapshot'])
                needs_baseline = snapshot.get('needs_baseline', bool(state['held'])) or self.migration_pending_for(aid)
                if not state['held'] and not needs_baseline:
                    return state['revision']
                if (self.db.execute("SELECT 1 FROM ai_runs WHERE account_id=? AND status='running'", (aid,)).fetchone() or
                        self.db.execute("SELECT 1 FROM deliveries WHERE account_id=? AND status='sending'", (aid,)).fetchone()):
                    raise ConversationError('compatibility_busy', '等待在途任务结束后自动继续。', 409)
                if needs_baseline:
                    recovery = snapshot.get('reply_resume', {})
                    same_binding = bool(state['fingerprint'] and recovery.get('fingerprint') == state['fingerprint']
                                        and not self.migration_pending_for(aid))
                    if not same_binding: self._compatibility_invalidate(aid)
                    for cid, in self.db.execute('SELECT conversation_id FROM conversation_preferences WHERE account_id=? AND ai_enabled=1', (aid,)).fetchall():
                        if same_binding:
                            self._delivery_resume(aid, cid, recovery.get('epochs', {}).get(cid))
                        else:
                            self._delivery_enable(aid, cid)
                snapshot.pop('reply_resume', None)
                snapshot['needs_baseline'] = False
                if self.migration_token:
                    snapshot['migration_token'] = self.migration_token
                revision = self._compatibility_write(aid, state['fingerprint'], False, '', snapshot)
            self.notify(dict(type='changed', account_id=aid, resources=['compatibility','ai_runs']))
            return revision
        return self.call(write)
