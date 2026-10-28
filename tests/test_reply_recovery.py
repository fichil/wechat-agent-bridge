"""Synthetic recovery, loopback authentication, and verified no-write waits."""
from dataclasses import replace
import http.client
import json
import threading
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock

from tests import test_prefix_reply_trial as trial
from tests import test_concurrent_console as concurrent
from wechat_agent_bridge.console_http import ConsoleServer
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.console_service import ConsoleService
from wechat_agent_bridge.console_store import ConsoleStore, digest
from tests.test_console import SyntheticProtector
from wechat_agent_bridge.adapters.console_send_trial import defer_prewrite_interruption


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        fixture = trial.PrefixTrialTests()
        self.addCleanup(fixture.doCleanups)
        self.s, self.rows, self.sender, self.clock = fixture.service()
        self.s.acceptance.test_budget_enforced = False
        self.s.trial_slots = ()
        self.rows.extend([trial.message(2, historical=True), trial.message(3, historical=True)])
        self.s.enable_prefix_trial('a', 'c', 'all_new_text')
        self.s.sync('a', 'c')
        self.reply = Mock(wraps=self.s.provider.reply)
        self.s.provider.reply = self.reply

    def recover(self, events=None, recovery_id='synthetic-recovery'):
        return self.s.recover_reply('a', 'c', events or ['2', '3'], recovery_id)

    def test_historical_batch_once_then_new_message_and_echo(self):
        baseline = self.s.status()['execution']['baselines'][0]
        self.assertEqual(baseline['message_count'], 3)
        self.clock.advance(4); self.s.tick(); self.sender.assert_not_called()
        result = self.recover()
        self.assertEqual(result['message_count'], 2)
        self.assertTrue(self.recover(['3', '2'])['replayed'])
        self.clock.advance(4); self.s.tick()
        self.sender.assert_called_once(); self.reply.assert_called_once()
        self.assertEqual(self.recover()['status'], 'ui_confirmed')
        sent = self.sender.call_args.args[2]
        self.rows.append(replace(trial.message(4), text=sent, own=None))
        self.s.sync('a', 'c'); self.clock.advance(15); self.s.tick()
        self.sender.assert_called_once()
        self.rows.append(trial.message(5)); self.s.sync('a', 'c')
        self.clock.advance(4); self.s.tick()
        self.assertEqual(self.sender.call_count, 2)
        with self.assertRaisesRegex(ConsoleError, 'already_claimed|prior_attempt_requires_review'):
            self.recover(recovery_id='different-id')

    def test_pending_restart_and_duplicate_never_recreate(self):
        original = self.recover()
        restarted = ConsoleService(self.s.adapter, self.s.store, clock=self.clock, acceptance=self.s.acceptance)
        restarted.discover()
        self.assertTrue(restarted.paused)
        result = restarted.recover_reply('a', 'c', ['2', '3'], 'synthetic-recovery')
        self.assertTrue(result['replayed'])
        self.assertEqual(result['job_id'], original['job_id'])
        self.assertEqual(result['status'], 'cancelled_on_restart')
        self.sender.assert_not_called(); self.reply.assert_not_called()

    def test_live_messages_and_recovery_keep_separate_batches_without_starvation(self):
        self.rows.append(trial.message(4)); self.s.sync('a', 'c')
        result = self.recover()
        self.clock.advance(1)
        self.rows.append(trial.message(5)); self.s.sync('a', 'c')
        jobs = self.s.store.values('job')
        self.assertEqual(len(jobs), 2)
        recovery = next(j for j in jobs if j['id'] == result['job_id'])
        regular = next(j for j in jobs if not j.get('recovery_id'))
        self.assertEqual(recovery['events'], ['2', '3'])
        self.assertEqual(regular['events'], ['4', '5'])
        self.clock.advance(4); self.s.tick()
        self.assertEqual(self.recover()['status'], 'ui_confirmed')
        self.clock.advance(11); self.s.tick()
        self.assertEqual(self.sender.call_count, 2)

    def test_wrong_scope_missing_event_invalid_input_and_conflicting_id(self):
        for args in [('other', 'c', ['2'], 'r'), ('a', 'other', ['2'], 'r'),
                     ('a', 'c', ['missing'], 'r'), ('a', 'c', ['2', '2'], 'r'),
                     ('a', 'c', ['2'], '../invalid')]:
            with self.assertRaises(ConsoleError): self.s.recover_reply(*args)
        self.recover()
        with self.assertRaisesRegex(ConsoleError, 'id_conflict'):
            self.recover(['1'])
        self.sender.assert_not_called()

    def test_file_reopen_preserves_recovery_tombstones_and_cancellation(self):
        self.recover()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'synthetic.sqlite3'
            target = ConsoleStore(path, SyntheticProtector())
            self.s.store.db.backup(target.db); target.close()
            reopened = ConsoleStore(path, SyntheticProtector())
            try:
                restarted = ConsoleService(self.s.adapter, reopened, clock=self.clock, acceptance=self.s.acceptance)
                restarted.discover()
                result = restarted.recover_reply('a', 'c', ['2', '3'], 'synthetic-recovery')
                self.assertEqual(result['status'], 'cancelled_on_restart')
                self.assertTrue(result['replayed'])
                self.assertEqual(len(reopened.values('reply_recovery_event')), 2)
                self.assertEqual(len(reopened.values('job')), 1)
            finally:
                reopened.close()

    def test_revocation_and_unknown_never_allow_recovery(self):
        self.s.grants['a'].upload = False
        with self.assertRaises(ConsoleError): self.recover()
        self.s.grants['a'].upload = True
        self.s.store.put('send_block', digest('a', 'c'), {'reason': 'unknown_send_requires_review'})
        with self.assertRaises(ConsoleError): self.recover()
        self.sender.assert_not_called()

    def test_own_echo_opaque_and_ineligible_rows_rejected(self):
        for change in (dict(own=True), dict(kind='unparsed'), dict(upload_eligible=False)):
            row = replace(trial.message('bad-'+str(len(self.rows)), historical=True), **change)
            self.rows.append(row); self.s.sync('a', 'c')
            with self.assertRaisesRegex(ConsoleError, 'ineligible'):
                self.recover([row.id], 'bad-'+row.id)
        self.s.store.put('outbound_echo', digest('a', 'c', self.rows[1].text), {'request_id': 'old'})
        with self.assertRaisesRegex(ConsoleError, 'ineligible'): self.recover()

    def test_recovery_transaction_rollback_leaves_no_tombstone_or_job(self):
        self.s.store.db.execute("CREATE TRIGGER reject_recovery BEFORE INSERT ON records WHEN NEW.bucket='reply_recovery_event' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(Exception): self.recover()
        self.assertIsNone(self.s.store.get('job', digest('reply-recovery', 'synthetic-recovery')))
        self.assertEqual(self.s.store.values('reply_recovery_event'), [])
        self.s.store.db.execute('DROP TRIGGER reject_recovery')
        self.assertEqual(self.recover()['status'], 'pending')

    def test_existing_draft_preserves_reply_and_unknown_stays_terminal(self):
        self.recover()
        original = self.s.adapter.inspector
        self.s.adapter.inspector = lambda *a: dict(original(*a), has_draft=True)
        self.clock.advance(4); self.s.tick()
        self.assertEqual(self.recover()['status'], 'ready')
        self.sender.assert_not_called(); self.reply.assert_called_once()
        self.s.adapter.inspector = original
        self.sender.return_value = dict(status='unknown', attempted=True)
        self.s.tick('send'); self.clock.advance(30); self.s.tick('send')
        self.sender.assert_called_once()
        self.assertEqual(self.s.store.values('job')[0]['status'], 'unknown')

    def test_http_requires_cookie_origin_csrf_and_returns_same_task(self):
        server = ConsoleServer(self.s)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def request(body, headers):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            connection.request('POST', '/api/reply-recovery', json.dumps(body),
                {'Origin': server.origin, 'Content-Type': 'application/json', **headers})
            response = connection.getresponse(); result = response.status, json.loads(response.read())
            connection.close(); return result
        body = dict(account='a', conversation='c', message_ids=['2', '3'], recovery_id='http-recovery')
        cookie = getattr(server, 'cookie_name', 'bridge_session')+'='+server.session
        auth = {'Cookie': cookie, 'X-CSRF-Token': server.csrf}
        try:
            self.assertEqual(request(body, {})[0], 403)
            self.assertEqual(request(body, {'Cookie': cookie})[0], 403)
            self.assertEqual(request(body, {**auth, 'Origin': 'https://evil.invalid'})[0], 403)
            self.assertEqual(request(body, auth)[0], 200)
            self.assertTrue(request(body, auth)[1]['replayed'])
        finally:
            server.shutdown(); server.server_close(); thread.join(2)


class PrewriteWaitTests(unittest.TestCase):
    def harness(self):
        fixture = concurrent.ConcurrentConsoleTests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.qualify_synthetic_channel()
        fixture.sender.return_value = dict(status='deferred', attempted=False,
            no_write_verified=True, reason='console_send_waiting_for_idle')
        return fixture

    def test_verified_wait_reuses_generation_then_sends_once_with_new_attempt_id(self):
        f = self.harness(); f.new(1); f.s.tick()
        job = f.store.values('job')[0]
        self.assertEqual(job['status'], 'ready')
        self.assertIn('reply_text', job)
        self.assertEqual(f.store.outcomes()[0]['status'], 'deferred')
        f.s.tick('send'); f.sender.assert_called_once()
        f.clock.advance(11); f.sender.return_value = dict(status='ui_confirmed', attempted=True)
        f.s.tick('send'); f.s.tick('send')
        self.assertEqual(f.sender.call_count, 2); f.reply.assert_called_once()
        self.assertNotEqual(f.sender.call_args_list[0].args[3], f.sender.call_args_list[1].args[3])
        self.assertEqual(f.store.values('job')[0]['status'], 'ui_confirmed')

    def test_unproven_or_attempted_deferral_fails_closed(self):
        for change in (dict(no_write_verified=False), dict(attempted=True), dict(reason='arbitrary body')):
            f = self.harness(); f.sender.return_value.update(change)
            f.new(1); f.s.tick(); f.clock.advance(20); f.s.tick()
            f.sender.assert_called_once()
            self.assertEqual(f.store.values('job')[0]['status'], 'unknown')

    def test_plain_failure_is_terminal_and_restart_cancels_verified_wait(self):
        f = self.harness(); f.sender.return_value = dict(status='failed', attempted=False)
        f.new(1); f.s.tick(); f.clock.advance(20); f.s.tick()
        f.sender.assert_called_once()
        g = self.harness(); g.new(1); g.s.tick()
        restarted = ConsoleService(g.adapter, g.store, clock=g.clock)
        self.assertTrue(restarted.paused)
        self.assertEqual(g.store.values('job')[0]['status'], 'cancelled_on_restart')

    def test_native_deferral_requires_no_claim_worker_exit_and_clean_recovery(self):
        report = dict(status='failed', attempted=False, worker_exited=True, reason='desktop_event_change_observed')
        self.assertEqual(defer_prewrite_interruption(report, staged=False, invoked=False, pending=None)['status'], 'deferred')
        for arguments in (dict(staged=True, invoked=False, pending=None),
                          dict(staged=False, invoked=True, pending=None),
                          dict(staged=False, invoked=False, pending={'pending': True})):
            self.assertEqual(defer_prewrite_interruption(report, **arguments)['status'], 'failed')
        for change in (dict(worker_exited=False), dict(attempted=True), dict(recovery_required=True),
                       dict(preflight={'cleanup': {}}), dict(reason='target_mismatch')):
            self.assertEqual(defer_prewrite_interruption({**report, **change}, staged=False, invoked=False, pending=None)['status'], 'failed')
