"""Wakeup and latency acceptance with fake sources, providers and transports."""
from collections import deque
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import threading
import time
from unittest.mock import Mock, patch

from backend.app.storage import now
from test_roles import RoleTestCase
import test_ai_execution as execution
import test_delivery as delivery
from test_message_api import message
from test_sync_lanes import until


class ReplyWakeTests(RoleTestCase):
    configured = execution.ExecutionTests.configured
    ingest = execution.ExecutionTests.ingest
    start = execution.ExecutionTests.start
    wait_run = execution.ExecutionTests.wait_run
    delta = delivery.DeliveryTests.delta

    def start_loops(self, result='sent'):
        self.transport = Mock()
        self.transport.inspect.return_value = dict(ready=True)
        self.transport.send.return_value = dict(status=result, code='synthetic')
        sender = self.app.state.sender
        sender.transport = self.transport
        sender.binding = lambda aid: dict(account='synthetic', pid=1, created=1)
        for service in (self.app.state.ai, sender):
            service.thread = threading.Thread(target=service.loop, daemon=True)
            service.thread.start()

    def test_exact_one_second_trailing_window_and_reply_priority(self):
        self.configured(); self.update(index=1, ai_enabled=True)
        self.ingest(account=0)
        self.delta(account=1)
        first = self.store.call(lambda: self.store.db.execute('SELECT last_trigger_at FROM delivery_sessions WHERE account_id=?', (self.ids[1],)).fetchone()[0])
        with patch('backend.app.delivery_storage.time.time', return_value=first+.999):
            self.assertEqual([c['account_id'] for c in self.store.ai_candidates()], [self.ids[0]])
        with patch('backend.app.delivery_storage.time.time', return_value=first+1.001):
            self.assertEqual([c['account_id'] for c in self.store.ai_candidates()], [self.ids[1], self.ids[0]])
        self.delta(2, account=1)
        second = self.store.call(lambda: self.store.db.execute('SELECT last_trigger_at FROM delivery_sessions WHERE account_id=?', (self.ids[1],)).fetchone()[0])
        self.assertGreater(second, first)
        with patch('backend.app.delivery_storage.time.time', return_value=second+.999):
            self.assertFalse(any(c['account_id'] == self.ids[1] for c in self.store.ai_candidates()))

    def test_failed_images_do_not_starve_images_after_first_four(self):
        self.configured()
        for index in range(1, 7): self.ingest(index, type='image', content={})
        with patch.object(self.app.state.bridge.messages, 'start', return_value={}) as start:
            self.app.state.ai.request_images(self.ids[0], self.cids[0])
            self.app.state.ai.request_images(self.ids[0], self.cids[0])
            self.app.state.ai.request_images(self.ids[0], self.cids[0])
        self.assertEqual(start.call_count, 6)
        self.assertEqual(len({call.args[0].message_id for call in start.call_args_list}), 6)

    def test_concurrent_history_cannot_overwrite_live_content(self):
        self.configured(); self.ingest()
        with self.store.source_snapshot(self.ids[0]) as revision:
            self.delta(content={'text': 'new live content'})
            stale = message(1); stale['content'] = {'text': 'old snapshot'}
            self.store.save_history(self.ids[0], self.cids[0], dict(items=[stale], has_more=False, issues=[], status='complete'), {},
                                    read_revision=revision)
            self.assertEqual(self.store.messages(self.ids[0], self.cids[0])['items'][0]['content']['text'], 'new live content')

    def test_history_first_live_confirmation_notifies_without_content_change(self):
        self.configured()
        when = (datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
        item = message(1)
        item.update(is_self=False, sent_at=when)
        item['_ref']['generation'] = 'g'
        self.store.save_history(self.ids[0], self.cids[0], dict(items=[item],has_more=False,issues=[],status='complete'), {})
        self.wait_run(self.start())
        events = []; previous = self.store.notify
        self.store.notify = lambda event: (events.append(event), previous(event))
        self.delta(sent_at=when)
        self.assertTrue(any('messages' in e.get('resources', []) for e in events))
        self.assertTrue(self.app.state.ai.wake.is_set())
        self.assertEqual(self.store.call(lambda: self.store.db.execute('SELECT count(*) FROM reply_triggers').fetchone()[0]), 1)

    def test_newer_history_survives_stale_live_snapshot_but_receives_trigger(self):
        self.configured()
        with self.store.source_snapshot(self.ids[0]) as revision:
            when = (datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
            self.ingest(sent_at=when, content={'text':'new history'})
            original = self.store.save_delta
            with patch.object(self.store, 'save_delta', side_effect=lambda aid,result: original(aid,result,read_revision=revision)):
                self.delta(sent_at=when, content={'text':'older live snapshot'})
            self.assertEqual(self.store.messages(self.ids[0],self.cids[0])['items'][0]['content']['text'], 'new history')
            self.assertEqual(self.store.call(lambda: self.store.db.execute('SELECT count(*) FROM reply_triggers').fetchone()[0]), 1)

    def test_ai_state_updates_do_not_hide_source_content_changes(self):
        self.configured(); self.ingest()
        with self.store.source_snapshot(self.ids[0]) as revision:
            self.wait_run(self.start())  # AI changes change_seq, not the source content timestamp.
            original = self.store.save_delta
            with patch.object(self.store, 'save_delta', side_effect=lambda aid,result: original(aid,result,read_revision=revision)):
                self.delta(content={'text':'edited while AI was running'})
            self.assertEqual(self.store.messages(self.ids[0],self.cids[0])['items'][0]['content']['text'], 'edited while AI was running')

    def test_source_conflict_versions_are_discarded_after_read_and_rollback(self):
        self.configured(); self.ingest()
        with self.store.source_snapshot(self.ids[0]) as revision:
            self.store.call(lambda: (self.store.db.execute("CREATE TRIGGER fail_checkpoint BEFORE INSERT ON sync_state BEGIN SELECT RAISE(ABORT,'synthetic'); END"), self.store.db.commit()))
            from backend.app.conversations import ConversationError
            with self.assertRaises(ConversationError): self.delta(content={'text':'rolled back'})
            self.assertEqual(self.store.source_versions, {})
            self.store.call(lambda: self.store.db.execute('DROP TRIGGER fail_checkpoint'))
            self.delta(content={'text':'committed'})
            self.assertTrue(self.store.source_versions)
        self.assertEqual(self.store.source_versions, {})
        self.assertEqual(self.store.source_reads, {})

    def test_paused_sessions_have_no_fast_lane_model_or_send(self):
        self.configured(); self.delta(); run = self.wait_run(self.start())
        self.store.call(lambda: (self.store.db.execute('UPDATE ai_sessions SET paused=1'), self.store.db.commit()))
        self.assertEqual(self.store.sync_targets(), {})
        self.assertEqual(self.store.ai_candidates(), [])
        self.assertEqual(self.store.delivery_candidates(), [])
        self.assertIsNone(self.store.claim_delivery(run['run_id'], 0))

    def test_unknown_send_remains_single_attempt_despite_repeated_wakeups(self):
        self.configured(); self.start_loops('unknown'); self.delta()
        until(lambda: self.transport.send.call_count == 1, timeout=4)
        until(lambda: not self.app.state.sender.pending)
        for _ in range(20):
            self.app.state.ai.wake.set(); self.app.state.sender.wake.set()
            time.sleep(.005)
        self.assertEqual(self.transport.send.call_count, 1)
        self.assertEqual(self.store.delivery_candidates(), [])

    def test_30_warm_samples_source_to_model_and_commit_to_send(self):
        self.configured()
        bridge = self.app.state.bridge
        bridge.discovery.scan()  # Fake probe supplied by RoleTestCase.
        bridge.scheduler.probe_due = float('inf')  # Never enumerate actual processes.
        inbox = deque(); inbox_lock = threading.Lock(); samples = []; current = {}
        source = self.store.conversation(self.ids[0], self.cids[0])['source_id']

        def changes(account, states, **options):
            with inbox_lock:
                item = inbox.popleft() if account.account_id == self.ids[0] and options['lane'] == 'realtime' and inbox else None
            if item is None:
                return dict(groups=[], unchanged=True, has_more=False)
            return dict(groups=[dict(source_id=source,shard=item['_ref']['shard'],table_name='Msg_synthetic',generation='g',
                last_id=item['_ref']['local_id'],baseline_id=0,items=[item])],issues=[],has_more=False)

        bridge.reader.changes = changes
        previous_notify = self.store.notify
        def notify(event):
            resources = event.get('resources', [])
            if current and event.get('account_id') == self.ids[0]:
                if 'messages' in resources and 'ai_runs' not in resources:
                    current.setdefault('archived', time.monotonic())
                if 'ai_runs' in resources:
                    # Notifications here run on the SQL owner after commit.
                    row = self.store.db.execute("SELECT 1 FROM deliveries WHERE account_id=? AND status='queued'", (self.ids[0],)).fetchone()
                    if row: current.setdefault('reply_committed', time.monotonic())
            previous_notify(event)
        self.store.notify = notify
        generate = self.provider.generate
        def model(*args):
            current['model_started'] = time.monotonic()
            return generate(*args)
        self.provider.generate = model
        self.start_loops()
        claim = self.store.claim_delivery
        def claimed(*args):
            result = claim(*args)
            if result: current['send_claimed'] = time.monotonic()
            return result
        self.store.claim_delivery = claimed
        bridge.thread = threading.Thread(target=bridge.loop, daemon=True)
        bridge.thread.start()
        for index in range(1, 31):
            until(lambda: not self.app.state.sender.pending)
            current = dict(available=time.monotonic())
            item = message(index)
            item.update(is_self=False, sent_at=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat())
            item['_ref']['generation'] = 'g'
            with inbox_lock: inbox.append(item)
            until(lambda: 'send_claimed' in current, timeout=7)
            sample = dict(source_to_model=current['model_started']-current['available'],
                          archive_to_model=current['model_started']-current['archived'],
                          reply_to_claim=current['send_claimed']-current['reply_committed'])
            self.assertGreaterEqual(sample['archive_to_model'], .95)
            self.assertLessEqual(sample['archive_to_model'], 2)
            self.assertGreaterEqual(sample['reply_to_claim'], 0)
            self.assertLessEqual(sample['reply_to_claim'], 1)
            samples.append(sample)
        until(lambda: not self.app.state.sender.pending)
        p95 = sorted(s['source_to_model'] for s in samples)[28]
        self.assertLessEqual(p95, 5)
        self.assertEqual(self.transport.send.call_count, 30)
        report = dict(environment='synthetic sources / fake provider / fake native transport', deployed=False,
                      sample_count=30, source_to_model_p95_seconds=p95, samples=samples)
        path = Path(__file__).resolve().parents[2]/'.local/latency/benchmark.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,indent=2),encoding='utf-8')
