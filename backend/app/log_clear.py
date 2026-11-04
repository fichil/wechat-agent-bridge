"""Bounded background log cleanup, independent of source and AI workers."""
from concurrent.futures import ThreadPoolExecutor
import threading

from .conversations import ConversationError


def clear_batches(targets):
    batch, size = [], 0
    for target in targets:
        if batch and (len(batch) >= 200 or size + target[2] > 4 * 1024 * 1024):
            yield batch
            batch, size = [], 0
        batch.append(target)
        size += target[2]
    if batch:
        yield batch


class LogClearService:
    def __init__(self, store):
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="log-clear")
        self.lock = threading.Lock()
        self.pending = {}
        self.stopped = threading.Event()

    def start(self, aid, scope):
        return self._start(aid, scope)[0]

    def query(self, aid, scope, task_id=None):
        task = self.store.log_clear_task(aid, scope, task_id)
        with self.lock:
            previous = self.pending.get(None if scope == "global" else aid)
        # A disk failure can prevent the worker from saving its terminal state.
        # Reconcile once storage is readable/writable again, without replaying it.
        if (task and task["status"] in ("queued", "running") and previous
                and previous[0] == task["task_id"] and previous[1].done()
                and previous[1].exception() is not None):
            return self.store.fail_log_clear(task["task_id"], "log_clear_failed")
        return task

    def _start(self, aid, scope):
        key = None if scope == "global" else aid
        with self.lock:
            if self.stopped.is_set():
                raise ConversationError("service_stopping", "服务正在停止。", 503)
            previous = self.pending.get(key)
            if previous and not previous[1].done():
                return self.store.log_clear_task(aid, scope, previous[0]), previous[1]
            if previous and previous[1].exception() is not None:
                self.store.fail_log_clear(previous[0], "log_clear_failed")
            task, targets = self.store.begin_log_clear(aid, scope)
            future = self.executor.submit(self.run, task, targets)
            self.pending[key] = task["task_id"], future
            return task, future

    def run(self, task, targets):
        try:
            for batch in clear_batches(targets):
                if self.stopped.is_set():
                    return self.store.fail_log_clear(task["task_id"], "log_clear_interrupted")
                task = self.store.log_clear_batch(task, batch)
            if not targets:
                task = self.store.log_clear_batch(task, [])
            return task
        except Exception:
            # Read committed progress; a failed batch must not advance counters.
            return self.store.fail_log_clear(task["task_id"], "log_clear_failed")

    def clear_and_wait(self, aid, scope):
        task, future = self._start(aid, scope)
        future.result()
        result = self.store.log_clear_task(aid, scope, task["task_id"])
        if result["status"] != "completed":
            raise ConversationError(result["error"], result["message"], 503)
        return dict(deleted_count=result["deleted_count"])

    def close(self):
        with self.lock:
            self.stopped.set()
        self.executor.shutdown(wait=True)
