"""All sources are synthetic. No test binds or sends to a real WeChat process."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app.runtime import Bridge
from backend.app.storage import Store
from backend.app.sync_scheduler import SyncScheduler
from wechat_conversations.client import LocalConversationReader
from wechat_account_probe.models import ProbeError
import test_conversation_workers as worker_fixtures
import test_incremental as incremental_fixtures
from test_conversations import accounts, Reader


def until(condition, timeout=3):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(.005)
    raise AssertionError("synthetic operation did not finish")


class ReaderLaneTests(unittest.TestCase):
    def test_blocked_background_and_account_leave_other_realtime_readers_free(self):
        entered, release = threading.Event(), threading.Event()
        workers = []

        def factory(instance, wxid):
            worker = Mock(instance=instance, wxid=wxid, used_at=time.monotonic())
            worker.process.poll.return_value = None
            def call(command, *args):
                if command == "message_image":
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("test release missing")
                return {}
            worker.call.side_effect = call
            workers.append(worker)
            return worker

        target = worker_fixtures.account()
        with patch("wechat_conversations.client.Worker", side_effect=factory):
            reader = LocalConversationReader()
            try:
                with ThreadPoolExecutor(3) as pool:
                    slow = pool.submit(reader.message_image, target, {})
                    self.assertTrue(entered.wait(1))
                    fast = pool.submit(reader.changes, target, [], lane="realtime", source_ids=["chat"])
                    self.assertEqual(fast.result(timeout=1), {})
                    # Reaping cannot close an in-flight worker even if its timestamp is old.
                    workers[0].used_at = time.monotonic()-100
                    reader._reap_idle()
                    workers[0].close.assert_not_called()
                    release.set(); slow.result(timeout=1)
            finally:
                release.set(); reader.close()
        self.assertEqual(len(workers), 2)
        for worker in workers:
            worker.close.assert_called_once()

    def test_pool_caps_idle_eviction_and_account_binding(self):
        made = []
        def factory(instance, wxid):
            worker = Mock(instance=instance, wxid=wxid, used_at=time.monotonic())
            worker.process.poll.return_value = None
            made.append(worker)
            return worker
        with patch("wechat_conversations.client.Worker", side_effect=factory):
            reader = LocalConversationReader()
            try:
                for i in range(6):
                    target = worker_fixtures.account().model_copy(deep=True)
                    target.account_id = str(i)
                    target.wxid = "synthetic_"+str(i)
                    reader.changes(target, [], lane="realtime")
                    reader.read(target)
                    self.assertLessEqual(sum(k[1] == "realtime" for k in reader._workers), 3)
                    self.assertLessEqual(sum(k[1] == "background" for k in reader._workers), 1)
                self.assertTrue(any(w.close.called for w in made))
            finally:
                reader.close()
            for worker in made:
                worker.close.assert_called_once()

    def test_same_account_lane_serializes_but_distinct_accounts_overlap(self):
        entered, release = threading.Event(), threading.Event()
        counts, maxima = {}, {}
        lock = threading.Lock()
        def factory(instance, wxid):
            worker = Mock(instance=instance, wxid=wxid, used_at=time.monotonic())
            worker.process.poll.return_value = None
            def call(*args):
                with lock:
                    counts[wxid] = counts.get(wxid, 0)+1
                    maxima[wxid] = max(maxima.get(wxid, 0), counts[wxid])
                if wxid == "wxid_synthetic":
                    entered.set(); release.wait(5)
                with lock:
                    counts[wxid] -= 1
                return {}
            worker.call.side_effect = call
            return worker
        with patch("wechat_conversations.client.Worker", side_effect=factory):
            reader = LocalConversationReader()
            a = worker_fixtures.account(); b = a.model_copy(deep=True)
            b.account_id = "other"; b.wxid = "other"
            try:
                with ThreadPoolExecutor(3) as pool:
                    first = pool.submit(reader.changes, a, [], lane="realtime")
                    self.assertTrue(entered.wait(1))
                    second = pool.submit(reader.changes, a, [], lane="realtime")
                    self.assertEqual(pool.submit(reader.changes, b, [], lane="realtime").result(1), {})
                    self.assertFalse(second.done())
                    release.set(); first.result(1); second.result(1)
            finally:
                release.set(); reader.close()
            self.assertEqual(maxima["wxid_synthetic"], 1)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.reader = Reader()
        self.reader.changes = Mock(return_value=dict(groups=[], unchanged=True))
        self.bridge = Bridge(self.store, accounts, self.reader, interval=.02)
        self.bridge.discovery.scan()
        self.ids = list(self.bridge.discovery.live_accounts)
        self.bridge.scheduler.probe_due = float("inf")

    def tearDown(self):
        self.bridge.close()

    def step(self):
        self.bridge.scheduler.targets_due = 0
        self.bridge.scheduler.step()

    def test_background_block_and_slow_account_do_not_block_second_account(self):
        release = threading.Event(); slow = threading.Event(); fast = threading.Event()
        self.store.sync_targets = lambda: {aid: ["chat"] for aid in self.ids}
        def read(account, states, **options):
            if options['lane'] == 'background' or account.account_id == self.ids[0]:
                slow.set(); release.wait(5)
            else:
                fast.set()
            return dict(groups=[], unchanged=True)
        self.reader.changes.side_effect = read
        try:
            self.step()
            self.assertTrue(slow.wait(1)); self.assertTrue(fast.wait(1))
            self.assertFalse(release.is_set())
            self.assertLessEqual(len(self.bridge.scheduler.active), 4)
        finally:
            release.set()

    def test_ownership_handoff_drains_both_lanes(self):
        release = threading.Event(); entered = threading.Event(); calls = []
        target = ["old"]
        self.store.sync_targets = lambda: {self.ids[0]: target[:]}
        def read(account, states, **options):
            if account.account_id == self.ids[0]:
                calls.append((options['lane'], options['source_ids']))
                if options['source_ids'] == ['old']:
                    entered.set(); release.wait(5)
            return dict(groups=[], unchanged=True)
        self.reader.changes.side_effect = read
        try:
            self.step(); self.assertTrue(entered.wait(1))
            target[:] = ["new"]
            for _ in range(3): self.step()
            self.assertTrue(all(s == ['old'] for _,s in calls))
            release.set()
            until(lambda: not self.bridge.scheduler.active)
            self.step()
            until(lambda: any(s == ['new'] for _,s in calls))
            self.assertEqual(self.bridge.scheduler.owners[self.ids[0]], frozenset(['new']))
        finally:
            release.set()

    def test_continuation_is_immediate_and_errors_back_off(self):
        self.store.sync_targets = lambda: {self.ids[0]: ["chat"]}
        self.reader.changes.return_value = dict(groups=[], unchanged=True, has_more=True)
        self.step(); until(lambda: not self.bridge.scheduler.active)
        before = self.reader.changes.call_count
        self.step(); until(lambda: not self.bridge.scheduler.active)
        self.assertGreater(self.reader.changes.call_count, before)
        self.reader.changes.side_effect = ProbeError("synthetic_failure")
        self.step(); until(lambda: not self.bridge.scheduler.active)
        failed_count = self.reader.changes.call_count
        self.step()
        self.assertEqual(self.reader.changes.call_count, failed_count)

    def test_job_status_failure_releases_ownership_without_running_source(self):
        self.store.sync_targets = lambda: {self.ids[0]: ["chat"]}
        original = self.store.job
        def job(tid, value=None, kind=None):
            if value and value['status'] == 'running':
                raise ProbeError('synthetic_job_failure')
            return original(tid, value, kind)
        with patch.object(self.store, 'job', side_effect=job):
            self.step()
            until(lambda: not self.bridge.scheduler.active)
        self.reader.changes.assert_not_called()
        scheduled = [(self.ids[0], 'realtime'), *[(aid, 'background') for aid in self.ids]]
        self.assertTrue(all(self.bridge.scheduler.due[key] > time.monotonic() for key in scheduled))


class ConversationIntervalTests(unittest.TestCase):
    def test_independent_intervals_updates_and_background_ownership(self):
        calls = []
        targets = {'a': {'fast': 2, 'slow': 60}}
        class Queue:
            lock = threading.RLock()
            pending = {}
            def submit(self, key, task, kind, work, completed):
                work()
                completed(dict(task, status='completed'))
        bridge = SimpleNamespace(interval=10, process_signature=None,
            store=SimpleNamespace(sync_targets=lambda: targets),
            discovery=SimpleNamespace(lock=threading.RLock(), live_accounts={'a': {}}),
            queue=Queue(), realtime_queue=Queue(),
            sync_account=lambda aid, **options: calls.append((aid, options)) or dict(issues=[], has_more=False))
        scheduler = SyncScheduler(bridge)
        scheduler.probe_due = float('inf')
        with patch('backend.app.sync_scheduler.time.monotonic', return_value=100) as clock:
            scheduler.step()
            self.assertEqual(calls[0][1], dict(lane='realtime', source_ids=['fast','slow']))
            for second in (102, 110, 120):
                clock.return_value = second
                scheduler.step()
            realtime = [options['source_ids'] for _, options in calls if options['lane']=='realtime']
            self.assertEqual(realtime, [['fast','slow'], ['fast'], ['fast'], ['fast']])
            self.assertTrue(all(options['source_ids']==['fast','slow'] for _,options in calls if options['lane']=='background'))
            # Settings changes apply even without any AI session or AI events.
            targets['a']['slow'] = 25
            scheduler.notify(dict(resources=['conversation_preferences']))
            clock.return_value = 124
            scheduler.step()
            self.assertEqual(calls[-1][1]['source_ids'], ['fast'])
            clock.return_value = 125
            scheduler.step()
            self.assertEqual(calls[-1][1]['source_ids'], ['slow'])


class IncrementalLaneTests(unittest.TestCase):
    setUp = incremental_fixtures.IncrementalTests.setUp
    tearDown = incremental_fixtures.IncrementalTests.tearDown
    write = incremental_fixtures.IncrementalTests.write
    tick = incremental_fixtures.IncrementalTests.tick
    messages = incremental_fixtures.IncrementalTests.messages

    def lane(self, lane, sources=None, save=True):
        result = self.reader.changes(dict(states=self.store.states(self.aid), lane=lane,
            source_ids=[self.source] if sources is None else sources), time.monotonic()+60)
        if save: self.store.save_delta(self.aid, result)
        return result

    def test_fast_waits_for_baseline_then_skips_metadata_and_other_tables(self):
        self.assertTrue(self.lane('realtime')['unchanged'])
        self.lane('background')
        self.assertEqual(self.messages(), [])
        self.write(range(1,9)); self.session.read.reset_mock()
        fast = self.lane('realtime')
        self.assertEqual(len(self.messages()), 5)
        self.assertTrue(all(g['table_name'] for g in fast['groups']))
        self.session.read.assert_not_called()
        self.assertTrue(self.lane('realtime')['unchanged'])
        self.lane('background')
        self.assertTrue(self.lane('background')['unchanged'])

    def test_uncommitted_worker_result_is_read_again_and_history_deduplicates(self):
        self.tick(); self.write(range(1,8))
        first = self.lane('realtime', save=False)
        second = self.lane('realtime')
        self.assertEqual(first['groups'], second['groups'])
        self.assertEqual(len(self.messages()), 4)

    def test_uncommitted_background_baseline_is_not_hidden_by_lane_cache(self):
        first = self.lane('background', save=False)
        second = self.lane('background')
        self.assertEqual(first['groups'], second['groups'])
        self.write(range(1,8)); self.lane('realtime')
        self.assertEqual(len(self.messages()), 4)

    def test_arrivals_during_bounded_backlog_are_not_marked_synced_early(self):
        self.tick(); self.write(range(1,155)); self.lane('realtime')
        self.write(range(1,165))
        for _ in range(6):
            result = self.lane('realtime')
            if not result['has_more']: break
        state = next(s for s in self.store.states(self.aid) if s['table_name'])
        self.assertEqual(state['last_id'], 164)
        self.assertIsNone(state['upper_id'])
        self.assertEqual(self.store.call(lambda: self.store.db.execute('SELECT count(*) FROM messages').fetchone()[0]), 161)

    def add_other(self, count):
        other = 'other@chatroom'
        db = sqlite3.connect(':memory:'); db.deserialize(self.reader.cache.images[self.shard])
        source_table = self.reader.table(self.source); table = self.reader.table(other)
        db.execute(f'CREATE TABLE "{table}" AS SELECT * FROM "{source_table}" WHERE local_id<=?', (count,))
        db.execute('INSERT INTO Name2Id(user_name) VALUES(?)', (other,))
        db.commit(); self.reader.cache.images[self.shard] = db.serialize(); db.close()
        return other

    def test_large_table_cannot_starve_other_target_and_background_excludes_both(self):
        other = self.add_other(3); self.tick()
        self.write(range(1,250)); self.add_other(9)
        targets = [self.source, other]
        self.assertFalse(any(g.get('items') for g in self.lane('background', targets)['groups']))
        first = self.lane('realtime', targets)
        self.assertTrue(first['has_more'])
        self.lane('realtime', targets)
        state = next(s for s in self.store.states(self.aid) if s.get('source_id') == other)
        self.assertEqual(state['last_id'], 9)
        for _ in range(8):
            result = self.lane('realtime', targets)
            if not result['has_more']: break
        self.assertFalse(result['has_more'])
        self.assertEqual(next(s for s in self.store.states(self.aid) if s.get('source_id') == self.source)['last_id'], 249)

    def test_handoff_resumes_committed_progress_and_does_not_reset_baseline(self):
        self.tick(); self.write(range(1,205)); self.lane('realtime')
        previous = next(s for s in self.store.states(self.aid) if s['table_name'])
        self.assertIsNotNone(previous['upper_id'])
        for _ in range(5): self.lane('background', [])
        current = next(s for s in self.store.states(self.aid) if s['table_name'])
        self.assertEqual(current['last_id'], 204)
        self.assertEqual(current['baseline_id'], previous['baseline_id'])

    def test_multiple_shards_drain_without_empty_continuation_loop(self):
        other = 'message/message_1.db'
        self.reader.cache.images[other] = self.reader.cache.images[self.shard]
        (self.root/'db_storage'/other).write_bytes(b'synthetic')
        self.reader.shards = lambda: [self.shard, other]
        for _ in range(3): self.lane('background')
        self.write(range(1,9))
        self.reader.cache.images[other] = self.reader.cache.images[self.shard]
        (self.root/'db_storage'/other).write_bytes(b'changed-size')
        self.assertTrue(self.lane('realtime')['has_more'])
        self.assertFalse(self.lane('realtime')['has_more'])
        self.assertTrue(self.lane('realtime')['unchanged'])
        for _ in range(4): result = self.lane('background')
        self.assertFalse(result['has_more'])
        self.assertTrue(result['unchanged'])
