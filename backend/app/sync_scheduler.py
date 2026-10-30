"""Bounded account/lane scheduling; ownership changes drain before handoff."""
import threading
import time
from uuid import uuid4

from .storage import now


class SyncScheduler:
    def __init__(self, bridge):
        self.bridge = bridge
        self.wake = threading.Event()
        self.lock = threading.RLock()
        self.owners = {}
        self.active = set()
        self.due = {}
        self.failures = {}
        self.targets = {}
        self.source_due = {}
        self.source_completed = {}
        self.continuations = {}
        self.targets_due = self.probe_due = 0

    def notify(self, event):
        if set(event.get("resources", ())) & {"conversation_preferences", "conversations", "accounts"}:
            # Called on the SQL owner thread: never wait on a scheduler lock here.
            self.targets_due = 0
            self.wake.set()

    def step(self):
        bridge = self.bridge
        current = time.monotonic()
        if current >= self.probe_due:
            self.probe_due = current + min(2, bridge.interval)
            bridge.compatibility.check_processes()
            from wechat_account_probe.native import enumerate_instances
            instances, _ = enumerate_instances()
            signature = tuple(sorted((i.pid, int(i.created)) for i in instances))
            if signature != bridge.process_signature:
                bridge.discovery.start()
        if current >= self.targets_due:
            targets = bridge.store.sync_targets()
            with self.lock:
                normalized = {aid: dict(values) if isinstance(values, dict) else {source: min(2, bridge.interval) for source in values}
                              for aid, values in targets.items()}
                for aid, values in normalized.items():
                    for source, interval in values.items():
                        if interval != self.targets.get(aid, {}).get(source):
                            self.source_due[(aid, source)] = self.source_completed.get((aid, source), current - interval) + interval
                self.targets = normalized
                self.targets_due = current + min(1, bridge.interval)
        with bridge.discovery.lock:
            accounts = list(bridge.discovery.live_accounts)
        with self.lock:
            for aid in accounts:
                wanted = frozenset(self.targets.get(aid, ()))
                if self.owners.get(aid) != wanted:
                    if any(key[0] == aid for key in self.active):
                        continue
                    self.owners[aid] = wanted
                    self.continuations.pop(aid, None)
                    for lane in ("realtime", "background"):
                        self.due[(aid, lane)] = 0
                for lane in ("realtime", "background"):
                    key = aid, lane
                    if (key in self.active or current < self.due.get(key, 0) or
                            (lane == "realtime" and not wanted)):
                        continue
                    sources = wanted
                    if lane == 'realtime':
                        sources = self.continuations.get(aid) or frozenset(s for s in wanted if current >= self.source_due.get((aid,s),0))
                        if not sources:
                            continue
                    queue = bridge.realtime_queue if lane == "realtime" else bridge.queue
                    with queue.lock:
                        if ("monitor", *key) in queue.pending:
                            continue
                    task = dict(task_id=uuid4().hex, account_id=aid, status="queued", started_at=now())
                    self.active.add(key)
                    try:
                        queue.submit(("monitor", *key), task, "monitor",
                                     lambda k=key, s=sources: self.run(k, s),
                                     completed=lambda task, k=key: self.finished(k, task))
                    except Exception:
                        self.active.discard(key)
                        self.due[key] = current + min(2, bridge.interval)

    def run(self, key, sources):
        aid, lane = key
        result = None
        failed = True
        try:
            result = self.bridge.sync_account(aid, lane=lane, source_ids=sorted(sources))
            failed = bool(result.get("issues"))
        except Exception:
            failed = True
            raise
        finally:
            with self.lock:
                attempts = self.failures.get(key, 0) + 1 if failed else 0
                self.failures[key] = min(attempts, 5)
                completed = time.monotonic()
                if lane == 'realtime':
                    if failed or result.get('has_more'):
                        self.continuations[aid] = sources
                    else:
                        self.continuations.pop(aid, None)
                        for source in sources:
                            self.source_completed[(aid,source)] = completed
                            self.source_due[(aid,source)] = completed + self.targets.get(aid,{}).get(source,2)
                    delay = min(30, 2 ** min(attempts, 5)) if failed else 0
                else:
                    delay = min(30, 2 ** min(attempts, 5)) if failed else (0 if result.get("has_more") else self.bridge.interval)
                self.due[key] = completed + delay

    def finished(self, key, task):
        with self.lock:
            # This also releases ownership if a job-status write failed before run().
            if task['status'] == 'failed' and self.due.get(key, 0) <= time.monotonic():
                self.due[key] = time.monotonic()+2
            self.active.discard(key)
        self.wake.set()

    def loop(self):
        while not self.bridge.stopped.is_set():
            self.wake.clear()
            try:
                self.step()
            except Exception:
                # Preserve checkpoints; a later pass retries transient scheduling errors.
                pass
            self.wake.wait(min(.1, self.bridge.interval))
