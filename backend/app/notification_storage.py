"""Durable account outage episodes and SMTP outbox, on the SQL owner thread."""
import json
import time
from uuid import uuid4
from . import secret_protection
from .conversations import ConversationError
from .notification_models import NotificationFields
from .role_storage import conflict


class NotificationStoreMixin:
    def _notification_settings(self):
        row = self.db.execute('SELECT * FROM notification_settings').fetchone()
        settings = {**NotificationFields().model_dump(), **json.loads(row['payload'])}
        latest = self.db.execute('SELECT * FROM notification_outbox ORDER BY created_at DESC,rowid DESC LIMIT 1').fetchone()
        return dict(**settings, revision=row['revision'], has_password=bool(row['secret']),
                    password_readable=bool(row['secret']) and secret_protection.verify(bytes(row['secret'])),
                    latest=self._notification_public(latest) if latest else None)

    @staticmethod
    def _notification_public(row):
        payload = json.loads(row['payload'])
        return dict(notification_id=row['notification_id'], status=row['status'], attempts=row['attempts'],
                    message=payload.get('message'), next_at=row['next_at'] if row['status']=='queued' else None,
                    stage=payload.get('stage'), error_code=payload.get('error_code'),
                    last_attempt_at=payload.get('last_attempt_at'))

    def notification_settings(self):
        return self.call(self._notification_settings)

    def save_notification_settings(self, body):
        password = body.password.get_secret_value() if body.password else ''
        secret = secret_protection.protect(password) if password else None
        fields = {k:getattr(body, k) for k in NotificationFields.model_fields}
        def write():
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                old = self.db.execute('SELECT * FROM notification_settings').fetchone()
                if old['revision'] != body.expected_revision: conflict()
                previous = {**NotificationFields().model_dump(), **json.loads(old['payload'])}
                identity_changed = (previous['host'].lower(), previous['username']) != (fields['host'].lower(), fields['username'])
                if identity_changed and old['secret'] and secret is None:
                    raise ConversationError('smtp_password_required', '更换 SMTP 服务器或用户名后，请重新填写授权码。', 422)
                chosen = secret if secret is not None else old['secret']
                if fields['enabled'] and (not chosen or not secret_protection.verify(bytes(chosen))):
                    raise ConversationError('smtp_password_required', '请填写可用的 SMTP 授权码。', 422)
                if json.loads(old['payload']) != fields or secret is not None:
                    self.db.execute('UPDATE notification_settings SET revision=revision+1,payload=?,secret=?', (json.dumps(fields), chosen))
                    revision = old['revision'] + 1
                    self.db.execute("UPDATE notification_outbox SET status='cancelled' WHERE kind='test' AND status='queued'")
                    if not fields['enabled'] or not previous['enabled']:
                        self.db.execute("UPDATE notification_outbox SET status='cancelled' WHERE kind='outage' AND status='queued'")
                        self.db.execute('DELETE FROM account_observations')
                        if fields['enabled']:
                            for row in self.db.execute("SELECT account_id,source_state FROM accounts WHERE source_state='online'").fetchall():
                                self._observe_account(row[0], row[1], 'notification_enabled')
                    else:
                        # Keep outage identity across a transport/configuration correction.
                        for row in self.db.execute("SELECT * FROM notification_outbox WHERE kind='outage' AND status IN ('queued','failed','cancelled')").fetchall():
                            payload = json.loads(row['payload'])
                            if self._notification_episode_current(row) and (row['status']=='queued' or payload.get('safe_to_retry')):
                                self.db.execute("UPDATE notification_outbox SET status='queued',settings_revision=?,next_at=? WHERE notification_id=?",
                                                (revision, time.time(), row['notification_id']))
            self.notify(dict(type='changed', resources=['notification_settings']))
            return self._notification_settings()
        return self.call(write)

    def _observe_account(self, aid, state, reason='', instances=None):
        """Called inside the account-state transaction; unknown never means logged out."""
        current = time.time()
        row = self.db.execute('SELECT * FROM account_observations WHERE account_id=?', (aid,)).fetchone()
        enabled = json.loads(self.db.execute('SELECT payload FROM notification_settings').fetchone()[0]).get('enabled', False)
        online = current if state == 'online' and enabled else row['last_online_at'] if row else None
        since = None if state == 'online' else row['abnormal_since'] if row else None
        episode = None if state == 'online' else row['event_id'] if row else None
        if enabled and state != 'online' and online is not None and since is None:
            since, episode = current, uuid4().hex
        self.db.execute('INSERT OR REPLACE INTO account_observations VALUES(?,?,?,?,?)', (aid,state,online,since,episode))
        if state == 'online':
            self.db.execute("UPDATE notification_outbox SET status='cancelled' WHERE account_id=? AND status='queued'", (aid,))
        if not row or row['state'] != state:
            if instances is None:
                instances = json.loads(self._compatibility_row(aid)['snapshot']).get('instances',[])
            self._log_put(dict(account_id=aid, category='source', operation='account_state', status='completed',
                               message='账号状态：'+state, reason=reason),
                          dict(previous=row['state'] if row else None, state=state, reason=reason,
                               instances=instances or [], observed_at=current, build_commit=self.build_commit))

    def queue_notification_test(self, revision):
        def write():
            settings = self._notification_settings()
            if settings['revision'] != revision: conflict()
            if not all(settings[k] for k in ('host','username','sender','recipient','password_readable')):
                raise ConversationError('smtp_incomplete', '请先保存完整的邮件配置和授权码。', 422)
            with self.db:
                # Double clicks share an in-flight test for this configuration.
                row = self.db.execute("SELECT * FROM notification_outbox WHERE kind='test' AND status IN ('queued','sending') AND settings_revision=?", (revision,)).fetchone()
                if not row:
                    identity = uuid4().hex
                    self.db.execute("INSERT INTO notification_outbox VALUES(?,NULL,'test','queued',0,?,?,?,?)", (identity,time.time(),revision,'{}',time.time()))
                    row = self.db.execute('SELECT * FROM notification_outbox WHERE notification_id=?', (identity,)).fetchone()
            self.notify(dict(type='changed', resources=['notification_settings']))
            return self._notification_public(row)
        return self.call(write)

    def _notification_episode_current(self, row):
        obs = self.db.execute('SELECT state,event_id FROM account_observations WHERE account_id=?', (row['account_id'],)).fetchone()
        return bool(obs and obs['state'] != 'online' and obs['event_id'] == row['notification_id'])

    def claim_notification(self):
        def write():
            if self.deployment_hold or self.migration_hold: return None
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                settings = self._notification_settings()
                if settings['enabled'] and getattr(self,'notification_scan_ready',False):
                    for obs in self.db.execute("SELECT o.*,a.payload FROM account_observations o JOIN accounts a USING(account_id) WHERE o.state!='online' AND o.abnormal_since<=? AND o.event_id IS NOT NULL", (time.time()-60,)).fetchall():
                        account = json.loads(obs['payload'])
                        payload = dict(nickname=account.get('nickname'), wxid=account.get('wxid'),
                                       last_online_at=obs['last_online_at'], abnormal_since=obs['abnormal_since'])
                        self.db.execute("INSERT OR IGNORE INTO notification_outbox VALUES(?,?,'outage','queued',0,?,?,?,?)", (obs['event_id'],obs['account_id'],time.time(),settings['revision'],json.dumps(payload),time.time()))
                for row in self.db.execute("SELECT * FROM notification_outbox WHERE status='queued' AND next_at<=? ORDER BY next_at LIMIT 32", (time.time(),)).fetchall():
                    if row['kind']=='outage' and not getattr(self,'notification_scan_ready',False): continue
                    obs = self.db.execute('SELECT * FROM account_observations WHERE account_id=?', (row['account_id'],)).fetchone()
                    if row['settings_revision'] != settings['revision'] or (row['kind']=='outage' and (not settings['enabled'] or not obs or obs['state']=='online' or obs['event_id']!=row['notification_id'])):
                        self.db.execute("UPDATE notification_outbox SET status='cancelled' WHERE notification_id=?", (row['notification_id'],))
                        continue
                    payload = {**json.loads(row['payload']), 'last_attempt_at':time.time()}
                    self.db.execute("UPDATE notification_outbox SET status='sending',attempts=attempts+1,payload=? WHERE notification_id=?",
                                    (json.dumps(payload),row['notification_id']))
                    secret = self.db.execute('SELECT secret FROM notification_settings').fetchone()[0]
                    return dict(row, settings=settings, encrypted_password=bytes(secret),
                                state=obs['state'] if obs else None, attempts=row['attempts']+1)
            return None
        return self.call(write)

    def notification_may_send(self, job):
        def query():
            row = self.db.execute('SELECT status FROM notification_outbox WHERE notification_id=?', (job['notification_id'],)).fetchone()
            settings = self.db.execute('SELECT revision,payload FROM notification_settings').fetchone()
            if not row or row['status'] != 'sending' or settings['revision'] != job['settings_revision']:
                return False
            if job['kind'] == 'test': return True
            obs = self.db.execute('SELECT state,event_id FROM account_observations WHERE account_id=?', (job['account_id'],)).fetchone()
            return bool(json.loads(settings['payload']).get('enabled') and obs and obs['state'] != 'online' and obs['event_id'] == job['notification_id'])
        return self.call(query)

    def finish_notification(self, job, status, message, retry=False):
        def write(status=status):
            with self.db:
                row = self.db.execute('SELECT * FROM notification_outbox WHERE notification_id=?', (job['notification_id'],)).fetchone()
                if not row or row['status'] != 'sending': return
                diagnostic = job.get('diagnostic', dict(retryable=retry, safe_to_retry=status in ('failed','cancelled')))
                payload = {**json.loads(row['payload']), **diagnostic, 'message':message}
                settings = self.db.execute('SELECT revision,payload FROM notification_settings').fetchone()
                enabled = json.loads(settings['payload']).get('enabled',False)
                current = enabled and self._notification_episode_current(row)
                changed = settings['revision'] != row['settings_revision']
                revision = row['settings_revision']
                delay = None
                if row['kind'] == 'outage' and status not in ('sent','unknown'):
                    if not current:
                        status = 'cancelled'
                    elif changed and payload.get('safe_to_retry'):
                        delay, revision = 0, settings['revision']
                    elif retry:
                        delay = (60,300,900,1800)[min(row['attempts']-1,3)]
                elif row['kind'] == 'test' and retry and not changed and row['attempts'] <= 3:
                    delay = (60,300,900)[row['attempts']-1]
                self.db.execute('UPDATE notification_outbox SET status=?,next_at=?,payload=?,settings_revision=? WHERE notification_id=?',
                                ('queued' if delay is not None else status,time.time()+(delay or 0),json.dumps(payload),revision,row['notification_id']))
            self.notify(dict(type='changed', resources=['notification_settings']))
        self.call(write)

    def _notification_recover(self):
        self.db.execute("UPDATE notification_outbox SET status='unknown',payload=json_set(payload,'$.message','发送期间服务中断，请核对邮箱；不会自动重复发送。') WHERE status='sending'")
        self.db.execute("""UPDATE notification_outbox SET status='queued',
            payload=json_set(payload,'$.safe_to_retry',json('true'),'$.retryable',json('true'))
            WHERE kind='outage' AND status='failed'
              AND json_extract(payload,'$.message')='无法连接邮件服务器或完成 TLS 验证。'""")
        # A startup scan establishes fresh facts before any outage mail is sent.
        self.notification_scan_ready = False
