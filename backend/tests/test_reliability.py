from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
from unittest.mock import Mock, patch
from uuid import uuid4
import httpx
import test_ai_execution as execution
from test_roles import RoleTestCase
from test_conversations import accounts
from backend.app.ai_execution import AiExecution
from backend.app.ai_provider import Provider
from backend.app.conversations import ConversationError
from backend.app.notification_models import NotificationUpdate
from backend.app.notifications import Notifications, send_mail
from backend.app.reliability_schema import RELIABILITY_SCHEMA
from backend.app.services import browser_values
from backend.app.storage import Store
from migration_helpers import remove_v10


class ReliabilityTests(RoleTestCase):
    configured = execution.ExecutionTests.configured
    ingest = execution.ExecutionTests.ingest

    def sql(self, query, args=()):
        def execute():
            with self.store.db: return self.store.db.execute(query,args).fetchall()
        return self.store.call(execute)

    def run_once(self):
        run=self.store.start_ai_run(self.ids[0],self.cids[0],uuid4().hex)
        self.app.state.ai.perform(self.ids[0],self.cids[0],run['run_id'])
        return self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])

    def retry_once(self, run):
        with patch('time.time',return_value=(run['next_retry_at'] or 0)+1):
            candidates=self.store.ai_retry_candidates()
            self.assertEqual(len(candidates),1)
            self.app.state.ai.perform(self.ids[0],self.cids[0],run['run_id'])
        return self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])

    def mail_config(self, enabled=True):
        old=self.store.notification_settings()
        body=NotificationUpdate(expected_revision=old['revision'],enabled=enabled,recipient='owner@example.test',
                                sender='bridge@example.test',host='smtp.example.test',username='bridge',password='synthetic-smtp-only')
        return self.store.save_notification_settings(body)

    def offline(self, aid=None):
        aid=aid or self.ids[0]
        self.store.set_state(aid,'offline')
        return self.store.account(aid)

    def test_default_extra_ten_attempts_then_pause(self):
        self.configured();self.ingest()
        self.assertEqual(self.preferences()['ai_max_retries'],10)
        with patch.object(self.provider,'generate',side_effect=ConversationError('ai_network_error','synthetic')) as generate:
            run=self.run_once()
            for attempt in range(1,11):
                self.assertEqual(run['status'],'queued')
                self.assertEqual(run['attempt_count'],attempt)
                self.assertFalse(self.store.ai_runs(*[self.ids[0],self.cids[0]])['paused'])
                run=self.retry_once(run)
            self.assertEqual(generate.call_count,11)
        self.assertEqual(run['status'],'failed')
        self.assertEqual(self.preferences()['memory_revision'],0)
        self.assertTrue(self.store.ai_runs(self.ids[0],self.cids[0])['paused'])
        self.assertFalse(self.sql('SELECT * FROM ai_request_snapshots'))

    def test_retry_success_keeps_snapshot_and_commits_once(self):
        self.configured();self.ingest()
        with patch.object(self.provider,'generate',side_effect=ConversationError('ai_response_invalid','synthetic')):
            run=self.run_once()
        snapshot=self.sql('SELECT payload FROM ai_request_snapshots')[0][0]
        self.assertNotIn('synthetic-test-only',snapshot)
        self.ingest(2)
        final=self.retry_once(run)
        self.assertEqual(final['status'],'completed')
        self.assertEqual(final['attempt_count'],2)
        self.assertEqual(self.preferences()['memory_revision'],1)
        self.app.state.ai.perform(self.ids[0],self.cids[0],run['run_id'])
        self.assertEqual(self.preferences()['memory_revision'],1)
        self.assertEqual(self.store.ai_runs(self.ids[0],self.cids[0])['pending_count'],1)

    def test_retry_zero_and_invalid_setting_values(self):
        self.configured();self.ingest()
        for value in (-1,101,True,1.5,None):
            self.assertEqual(self.update(self.preferences()['revision'],ai_max_retries=value).status_code,422)
        self.assertEqual(self.update(self.preferences()['revision'],ai_max_retries=0).status_code,200)
        with patch.object(self.provider,'generate',side_effect=ConversationError('ai_network_error','synthetic')):
            self.assertEqual(self.run_once()['status'],'failed')

    def test_wait_does_not_occupy_executor_and_uses_run_limit(self):
        self.configured();self.ingest()
        with patch.object(self.provider,'generate',side_effect=ConversationError('ai_network_error','synthetic')):
            run=self.run_once()
        self.assertFalse(self.app.state.ai.pending)
        self.assertFalse(self.store.ai_retry_candidates())
        with patch('time.time',return_value=run['next_retry_at']-2):
            self.assertEqual(self.store.ai_schedule_delay(),2)
        self.update(self.preferences()['revision'],ai_max_retries=0)
        self.assertEqual(self.retry_once(run)['max_retries'],10)

    def test_memory_or_configuration_change_stops_pending_retry(self):
        self.configured();self.ingest()
        with patch.object(self.provider,'generate',side_effect=ConversationError('ai_network_error','synthetic')):
            run=self.run_once()
        self.update(self.preferences()['revision'],memory='user memory')
        self.assertFalse(self.store.ai_retry_candidates())
        final=self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])
        self.assertEqual(final['status'],'conflicted')
        self.assertEqual(self.preferences()['memory'],'user memory')

    def test_disable_cancels_wait_without_sending(self):
        self.configured();self.ingest()
        with patch.object(self.provider,'generate',side_effect=ConversationError('ai_network_error','synthetic')):
            run=self.run_once()
        self.update(self.preferences()['revision'],ai_enabled=False)
        self.assertFalse(self.store.ai_retry_candidates())
        self.assertEqual(self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])['status'],'cancelled')
        self.assertFalse(self.sql('SELECT * FROM ai_request_snapshots'))

    def test_restart_preserves_remaining_budget_and_snapshot(self):
        self.configured();self.ingest()
        with patch.object(self.provider,'generate',side_effect=ConversationError('ai_network_error','synthetic')):
            run=self.run_once()
        copy=self.path.with_name('restart.sqlite3')
        def backup():
            with closing(sqlite3.connect(copy)) as target:self.store.db.backup(target)
        self.store.call(backup)
        with closing(Store(copy)) as restored:
            current=restored.ai_run(self.ids[0],self.cids[0],run['run_id'])
            self.assertEqual((current['status'],current['attempt_count']),('queued',1))
            ai=AiExecution(restored,Mock(),self.provider,automatic=False)
            try:ai.perform(self.ids[0],self.cids[0],run['run_id'])
            finally:ai.close()
            self.assertEqual(restored.ai_run(self.ids[0],self.cids[0],run['run_id'])['attempt_count'],2)
            self.assertEqual(restored.preferences(self.ids[0],self.cids[0])['memory_revision'],1)

    def test_offline_delete_removes_owned_rows_and_shared_references(self):
        self.configured();self.ingest();run=self.run_once()
        self.mail_config(); account=self.offline()
        result=self.post('accounts/delete',account_id=self.ids[0],expected_revision=account['revision'])
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(len(self.store.accounts()),1)
        for table in ('messages','ai_runs','conversations','conversation_preferences','diagnostic_logs','account_observations','notification_outbox'):
            self.assertFalse(self.sql('SELECT * FROM '+table+' WHERE account_id=?',(self.ids[0],)),table)
        self.assertEqual(len(self.sql('SELECT * FROM role_cards')),1)
        self.assertEqual(len(self.sql('SELECT * FROM api_profiles')),1)
        self.assertIsNone(self.store.finish_ai_run(self.ids[0],self.cids[0],run['run_id'],result={'memory':'late','reply':'late'}))
        self.store.log_write(dict(account_id=self.ids[0],category='ai',operation='late'))
        self.store.job('late',dict(account_id=self.ids[0],status='completed'),'monitor')
        self.assertFalse(self.sql('SELECT * FROM sync_jobs WHERE account_id=?',(self.ids[0],)))
        self.assertFalse(self.sql('SELECT * FROM diagnostic_logs WHERE account_id=?',(self.ids[0],)))
        new=self.store.save_accounts(browser_values(accounts()))['accounts'][0]['account_id']
        self.assertNotEqual(new,self.ids[0])
        self.assertFalse(self.sql('SELECT * FROM conversation_preferences WHERE account_id=?',(new,)))

    def test_delete_rejects_online_unknown_and_stale_revision(self):
        for state in ('online','unknown'):
            self.store.set_state(self.ids[0],state)
            account=self.store.account(self.ids[0])
            response=self.post('accounts/delete',account_id=self.ids[0],expected_revision=account['revision'])
            self.assertEqual(response.json()['code'],'account_not_offline')
        old=self.offline()
        self.store.set_state(self.ids[0],'online')
        self.assertEqual(self.post('accounts/delete',account_id=self.ids[0],expected_revision=old['revision']).json()['code'],'revision_conflict')

    def test_delete_rolls_back_all_children_and_fences_old_discovery(self):
        self.ingest();account=self.offline()
        self.sql('CREATE TABLE retained(account_id TEXT REFERENCES accounts)')
        self.sql('INSERT INTO retained VALUES(?)',(self.ids[0],))
        with self.assertRaises(ConversationError):self.store.delete_account(self.ids[0],account['revision'])
        self.assertEqual(len(self.sql('SELECT * FROM messages')),1)
        self.sql('DROP TABLE retained')
        generation=self.store.account_generation
        self.store.delete_account(self.ids[0],account['revision'])
        with self.assertRaises(ConversationError) as exc:self.store.save_accounts(browser_values(accounts()),generation)
        self.assertEqual(exc.exception.code,'account_scan_obsolete')
        self.assertEqual(len(self.store.accounts()),1)

    def test_mail_debounce_dedup_state_switch_and_new_episode(self):
        self.mail_config()
        with patch('time.time',return_value=1000):self.store.set_state(self.ids[0],'unknown','probe_failed')
        with patch('time.time',return_value=1059):self.assertIsNone(self.store.claim_notification())
        with patch('time.time',return_value=1060):job=self.store.claim_notification()
        self.assertEqual(job['state'],'unknown')
        self.store.finish_notification(job,'sent','accepted')
        self.offline()
        self.assertIsNone(self.store.claim_notification())
        with patch('time.time',return_value=1100):self.store.set_state(self.ids[0],'online')
        with patch('time.time',return_value=1200):self.offline()
        with patch('time.time',return_value=1260):second=self.store.claim_notification()
        self.assertNotEqual(second['notification_id'],job['notification_id'])

    def test_mail_historical_offline_is_not_armed_and_recovery_cancels(self):
        self.offline();self.mail_config()
        self.assertIsNone(self.store.claim_notification())
        self.store.set_state(self.ids[0],'online')
        with patch('time.time',return_value=1000):self.offline()
        with patch('time.time',return_value=1060):job=self.store.claim_notification()
        self.store.finish_notification(job,'failed','temporary',True)
        self.store.set_state(self.ids[0],'online')
        with patch('time.time',return_value=10**10):self.assertIsNone(self.store.claim_notification())

    def test_mail_encrypted_secret_omission_conflict_and_disabled_test(self):
        settings=self.mail_config(False)
        raw=self.sql('SELECT secret FROM notification_settings')[0][0]
        self.assertNotIn(b'synthetic-smtp-only',raw)
        fields={k:settings[k] for k in ('enabled','recipient','sender','host','port','username','security')}
        result=self.post('notification-settings/update',**fields,expected_revision=settings['revision'])
        self.assertEqual(result.status_code,200)
        self.assertNotIn('synthetic-smtp-only',result.text)
        self.assertEqual(raw,self.sql('SELECT secret FROM notification_settings')[0][0])
        self.assertEqual(self.post('notification-settings/update',**fields,expected_revision=999).status_code,409)
        queued=self.store.queue_notification_test(settings['revision'])
        self.assertEqual(queued,self.store.queue_notification_test(settings['revision']))
        with patch.object(self.app.state.notifications,'transport',return_value=('sent','accepted',False)) as send:
            self.app.state.notifications.tick()
            self.assertEqual(send.call_count,1)
        self.assertEqual(self.store.notification_settings()['latest']['status'],'sent')

    def test_mail_interrupted_send_is_unknown_and_not_replayed(self):
        self.mail_config(False);self.store.queue_notification_test(self.store.notification_settings()['revision'])
        job=self.store.claim_notification()
        self.assertIsNotNone(job)
        def recover():
            with self.store.db:self.store._notification_recover()
        self.store.call(recover)
        self.assertEqual(self.store.notification_settings()['latest']['status'],'unknown')
        self.assertIsNone(self.store.claim_notification())

    def test_mail_rechecks_recovery_deletion_and_settings_before_data(self):
        self.mail_config()
        for change in ('online', 'disabled', 'deleted'):
            self.mail_config()
            self.store.set_state(self.ids[0], 'online')
            with patch('time.time', return_value=1000): self.offline()
            with patch('time.time', return_value=1060): job = self.store.claim_notification()
            self.assertTrue(self.store.notification_may_send(job))
            client = Mock()
            client.mail.return_value = (250, b'ok')
            def recipient(_):
                if change == 'online': self.store.set_state(self.ids[0], 'online')
                elif change == 'disabled': self.mail_config(False)
                else:
                    account = self.store.account(self.ids[0])
                    self.store.delete_account(self.ids[0], account['revision'])
                return 250, b'ok'
            client.rcpt.side_effect = recipient
            job['can_send'] = lambda: self.store.notification_may_send(job)
            with patch('smtplib.SMTP_SSL', return_value=client): result = send_mail(job)
            self.assertEqual(result[0], 'cancelled')
            client.data.assert_not_called()
            self.store.finish_notification(job, *result)

    def test_smtp_disconnect_after_data_is_unknown_and_auth_error_is_final(self):
        self.mail_config(False);self.store.queue_notification_test(self.store.notification_settings()['revision'])
        job=self.store.claim_notification()
        import smtplib
        client=Mock();client.mail.return_value=(250,b'ok');client.rcpt.return_value=(250,b'ok')
        client.data.side_effect=smtplib.SMTPServerDisconnected('secret must never escape')
        with patch('smtplib.SMTP_SSL',return_value=client):result=send_mail(job)
        self.assertEqual((result[0],result[2]),('unknown',False));self.assertNotIn('secret',result[1])
        client.login.side_effect=smtplib.SMTPAuthenticationError(535,b'synthetic-smtp-only')
        with patch('smtplib.SMTP_SSL',return_value=client):result=send_mail(job)
        self.assertEqual((result[0],result[2]),('failed',False));self.assertNotIn('synthetic',result[1])
        client.login.side_effect=smtplib.SMTPNotSupportedError('synthetic-smtp-only')
        with patch('smtplib.SMTP_SSL',return_value=client):result=send_mail(job)
        self.assertEqual((result[0],result[2]),('failed',False));self.assertNotIn('synthetic',result[1])

    def test_v10_migration_failure_keeps_v9_and_account_data(self):
        copy=self.path.with_name('v9.sqlite3')
        def backup():
            with closing(sqlite3.connect(copy)) as db:self.store.db.backup(db)
        self.store.call(backup)
        with closing(sqlite3.connect(copy)) as db,db:
            remove_v10(db);db.execute('PRAGMA user_version=9')
        with patch('backend.app.reliability_schema.RELIABILITY_SCHEMA',(*RELIABILITY_SCHEMA,'INVALID SQL')):
            with self.assertRaises(ConversationError):Store(copy)
        with closing(sqlite3.connect(copy)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],9)
            self.assertEqual(db.execute('SELECT count(*) FROM accounts').fetchone()[0],2)
            self.assertNotIn('ai_max_retries',[r[1] for r in db.execute('PRAGMA table_info(conversation_preferences)')])
        with closing(Store(copy)) as restored:
            self.assertEqual(restored.preferences(self.ids[0],self.cids[0])['ai_max_retries'],10)

    def test_http_retry_classification_and_retry_after(self):
        profile=self.create_api()
        for status,body,expected in ((408,'timeout',True),(429,'rate limit',True),(503,'unavailable',True),
                                     (401,'bad key',False),(403,'denied',False),(404,'missing',False),
                                     (400,'invalid',False),(429,'insufficient_quota',False)):
            with self.subTest(status=status,body=body):
                provider=Provider(httpx.MockTransport(lambda request:httpx.Response(status,text=body,headers={'Retry-After':'125'})))
                with self.assertRaises(ConversationError) as exc:provider.generate(profile,'synthetic',dict(instructions='json',messages=[],memory=''),'schema')
                self.assertEqual(exc.exception.retryable,expected)
                self.assertEqual(exc.exception.retry_after,125 if expected else 0)
        self.configured();self.ingest()
        error=ConversationError('ai_provider_error','temporary');error.retryable=True;error.retry_after=125
        with patch('time.time',return_value=1000),patch.object(self.provider,'generate',side_effect=error):
            run=self.run_once()
            self.assertEqual(run['next_retry_at'],1000)  # Untouched backup bypasses the first provider's wait.
            self.app.state.ai.perform(self.ids[0],self.cids[0],run['run_id'])
            run=self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])
        self.assertEqual(run['next_retry_at'],1125)

    def test_config_change_and_compatibility_wait_do_not_spend_attempts(self):
        self.configured();self.ingest()
        with patch.object(self.provider,'generate',side_effect=ConversationError('ai_network_error','synthetic')):run=self.run_once()
        with patch.object(self.store,'_compatibility_blocked',return_value=True),patch('time.time',return_value=10**10):
            self.assertFalse(self.store.ai_retry_candidates())
        self.assertEqual(self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])['attempt_count'],1)
        self.store.save_ai_settings(self.store.ai_settings()['revision'],'new instructions')
        self.assertFalse(self.store.ai_retry_candidates())
        self.assertEqual(self.store.ai_run(self.ids[0],self.cids[0],run['run_id'])['error'],'ai_configuration_changed')

    def test_deleting_during_http_prevents_memory_and_capture_resurrection(self):
        self.configured();self.ingest()
        entered,release=threading.Event(),threading.Event()
        original=self.provider.generate
        def blocked(*args):
            entered.set();self.assertTrue(release.wait(5));return original(*args)
        with patch.object(self.provider,'generate',side_effect=blocked):
            run=self.app.state.ai.start(self.ids[0],self.cids[0],uuid4().hex)
            self.assertTrue(entered.wait(3));account=self.offline()
            log_id=self.store.log_write(dict(account_id=self.ids[0],category='ai',operation='conversation',status='running'))
            self.store.delete_account(self.ids[0],account['revision'])
            self.store.log_write(dict(log_id=log_id,status='completed'),dict(private='must not return'))
            release.set()
            self.app.state.ai.executor.shutdown(wait=True)
        self.assertFalse(self.sql('SELECT * FROM ai_runs WHERE run_id=?',(run['run_id'],)))
        self.assertFalse(self.sql('SELECT * FROM diagnostic_logs WHERE log_id=?',(log_id,)))
        self.assertFalse(self.sql('SELECT * FROM diagnostic_details WHERE log_id=?',(log_id,)))

    def test_mail_temporary_errors_retry_three_times_then_stop(self):
        self.mail_config(False);self.store.queue_notification_test(self.store.notification_settings()['revision'])
        current=10**9
        for attempt,delay in enumerate((60,300,900,None),1):
            current=max(current,self.store.notification_settings()['latest']['next_at'] or 0)+1
            with patch('time.time',return_value=current):
                job=self.store.claim_notification();self.assertEqual(job['attempts'],attempt)
                self.store.finish_notification(job,'failed','temporary',True)
                latest=self.store.notification_settings()['latest']
                self.assertEqual(latest['status'],'queued' if delay else 'failed')
                self.assertEqual(latest['next_at'],current+delay if delay else None)
        self.assertIsNone(self.store.claim_notification())

    def test_restart_keeps_sent_episode_and_cancels_pending_on_disable(self):
        self.mail_config()
        with patch('time.time',return_value=1000):self.offline()
        with patch('time.time',return_value=1060):job=self.store.claim_notification()
        self.store.finish_notification(job,'sent','accepted')
        copy=self.path.with_name('mail-restart.sqlite3')
        def backup():
            with closing(sqlite3.connect(copy)) as target:self.store.db.backup(target)
        self.store.call(backup)
        with closing(Store(copy)) as restored:
            restored.set_state(self.ids[0],'offline')
            restored.notification_scan_ready=True
            self.assertIsNone(restored.claim_notification())
        self.store.set_state(self.ids[0],'online')
        with patch('time.time',return_value=2000):self.offline()
        with patch('time.time',return_value=2060):job=self.store.claim_notification()
        self.store.finish_notification(job,'failed','temporary',True)
        self.mail_config(False)
        self.assertIsNone(self.store.claim_notification())
