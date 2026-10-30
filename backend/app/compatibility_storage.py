"""Durable compatibility holds, independent of conversation/user pause switches."""
from datetime import datetime, timezone
import json

from .conversations import ConversationError

COMPATIBILITY_SCHEMA = (
    'CREATE TABLE compatibility_state(scope TEXT PRIMARY KEY, revision INTEGER NOT NULL, fingerprint TEXT NOT NULL, held INTEGER NOT NULL, reason TEXT NOT NULL, snapshot TEXT NOT NULL)',
)


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
    migration_hold = False

    def _compatibility_row(self, scope):
        row = self.db.execute('SELECT * FROM compatibility_state WHERE scope=?', (scope,)).fetchone()
        return dict(row) if row else dict(scope=scope, revision=0, fingerprint='', held=1, reason='not_checked', snapshot='{}')

    def compatibility_rows(self):
        return self.call(lambda: [dict(r) for r in self.db.execute('SELECT * FROM compatibility_state ORDER BY scope')])

    def _compatibility_blocked(self, aid):
        return self.migration_hold or (self.compatibility_enforced and (
            self._compatibility_row('*')['held'] or self._compatibility_row(aid)['held']))

    def compatibility_blocked(self, aid):
        return self.call(self._compatibility_blocked, aid)

    def _compatibility_require(self, aid):
        if self._compatibility_blocked(aid):
            raise ConversationError('compatibility_paused', '自动处理已暂停，请完成兼容检查、发送验收并明确恢复。', 409)

    def _compatibility_write(self, scope, fingerprint, held, reason, snapshot):
        old = self._compatibility_row(scope)
        encoded = json.dumps(snapshot, sort_keys=True, separators=(',', ':'))
        value = (fingerprint, int(held), reason, encoded)
        if value == (old['fingerprint'], old['held'], old['reason'], old['snapshot']):
            return old['revision']
        revision = old['revision']+1
        self.db.execute('INSERT OR REPLACE INTO compatibility_state VALUES(?,?,?,?,?,?)',
                        (scope, revision, *value))
        return revision

    def _compatibility_invalidate(self, aid):
        """Invalidate eligibility, never change a submitted send into retryable work."""
        timestamp = datetime.now(timezone.utc).isoformat()
        self.db.execute('UPDATE ai_sessions SET epoch=epoch+1,auto_pending=0 WHERE account_id=?', (aid,))
        self.db.execute("UPDATE deliveries SET status='cancelled',code='compatibility_changed' WHERE account_id=? AND status IN ('queued','failed')", (aid,))
        for row in self.db.execute("SELECT run_id,payload FROM ai_runs WHERE account_id=? AND status='queued'", (aid,)).fetchall():
            value = json.loads(row['payload'])
            value.update(status='cancelled', completed_at=timestamp, message='微信兼容状态变化，旧任务已取消。')
            self.db.execute("UPDATE ai_runs SET status='cancelled',payload=? WHERE run_id=?", (json.dumps(value, ensure_ascii=False), row['run_id']))
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
                        self._compatibility_invalidate(aid)
                    self._compatibility_write(aid, old['fingerprint'], True, reason, json.loads(old['snapshot']))
            self.notify(dict(type='changed', resources=['compatibility', 'ai_runs']))
        self.call(write)

    def compatibility_pause(self, aid, reason):
        def write():
            with self.db:
                old = self._compatibility_row(aid)
                if not old['held']:
                    self._compatibility_invalidate(aid)
                self._compatibility_write(aid, old['fingerprint'], True, reason, json.loads(old['snapshot']))
            self.notify(dict(type='changed', account_id=aid, resources=['compatibility','ai_runs']))
        self.call(write)

    def compatibility_observe(self, states, unresolved):
        if not self.compatibility_enforced:
            return
        def write():
            with self.db:
                self._compatibility_write('*', '', bool(unresolved), 'unidentified_client' if unresolved else '', dict(instances=unresolved))
                for aid, value in states.items():
                    old = self._compatibility_row(aid)
                    changed = old['fingerprint'] != value['fingerprint']
                    value = dict(value)
                    previous_send = json.loads(old['snapshot']).get('send')
                    if not changed and previous_send is not None:
                        value['send'] = previous_send
                    held = bool(old['held'] or changed or unresolved or not value['readable'])
                    reason = ('unidentified_client' if unresolved else 'unsupported_build' if not value['readable'] else
                              'build_changed' if changed else old['reason'] if held else '')
                    if changed or (held and not old['held']):
                        self._compatibility_invalidate(aid)
                    self._compatibility_write(aid, value['fingerprint'], held, reason, value)
                for row in self.db.execute('SELECT account_id FROM accounts').fetchall():
                    aid = row[0]
                    if aid not in states:
                        old = self._compatibility_row(aid)
                        if not old['held']:
                            self._compatibility_invalidate(aid)
                        self._compatibility_write(aid, old['fingerprint'], True,
                                                  'unidentified_client' if unresolved else 'source_offline', json.loads(old['snapshot']))
            self.notify(dict(type='changed', resources=['compatibility', 'ai_runs']))
        self.call(write)

    def compatibility_send_state(self, aid, ready, code):
        """Include observed native readiness in the revision used by resume CAS."""
        def write():
            with self.db:
                old = self._compatibility_row(aid)
                snapshot = json.loads(old['snapshot'])
                snapshot['send'] = dict(ready=bool(ready), code=code)
                held = bool(old['held'] or not ready)
                if held and not old['held']:
                    self._compatibility_invalidate(aid)
                self._compatibility_write(aid, old['fingerprint'], held,
                                          code if not ready else old['reason'], snapshot)
                return self._compatibility_row(aid)
        return self.call(write)

    def compatibility_resume(self, aid, expected_revision):
        def write():
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                state = self._compatibility_row(aid)
                if state['revision'] != expected_revision:
                    raise ConversationError('revision_conflict', '兼容状态已变化，请重新检查。', 409)
                if self._compatibility_row('*')['held'] or self.migration_hold:
                    raise ConversationError('compatibility_paused', '仍有未确认的客户端或等待恢复的迁移。', 409)
                if not state['held']:
                    return state['revision']
                if (self.db.execute("SELECT 1 FROM ai_runs WHERE account_id=? AND status='running'", (aid,)).fetchone() or
                        self.db.execute("SELECT 1 FROM deliveries WHERE account_id=? AND status='sending'", (aid,)).fetchone()):
                    raise ConversationError('compatibility_busy', '请等待已开始的任务正常结束后恢复。', 409)
                self._compatibility_invalidate(aid)
                for cid, in self.db.execute('SELECT conversation_id FROM conversation_preferences WHERE account_id=? AND ai_enabled=1', (aid,)).fetchall():
                    self._delivery_enable(aid, cid)
                revision = self._compatibility_write(aid, state['fingerprint'], False, '', json.loads(state['snapshot']))
            self.notify(dict(type='changed', account_id=aid, resources=['compatibility','ai_runs']))
            return revision
        return self.call(write)
