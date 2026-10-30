"""Separate realtime and background source lanes; cache reads bypass both."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import threading
import time
from uuid import uuid4

from wechat_account_probe.models import ProbeError
from wechat_conversations.reader import IDENTITY_ERRORS
from .conversations import ConversationError
from .models import Account, AccountSnapshot, ScanTask
from .conversation_models import ConversationPage, ConversationRefresh, Exclusions
from .message_models import MessageReadTask, MessagePage, MessageImage
from .services import browser_values
from .storage import now, dump
from .diagnostics import record_source, record_delta_groups


QUEUE_WAIT = ContextVar("source_queue_wait", default=0)


class WorkQueue:
    def __init__(self, store, workers=1, name="source-background"):
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)
        self.lock = threading.RLock()
        self.pending = {}
        self.closed = False

    def submit(self, key, task, kind, work, *, completed=None):
        with self.lock:
            if self.closed:
                raise ConversationError("service_stopping", "服务正在停止。", 503)
            if key in self.pending:
                return self.store.job(self.pending[key])
            if len(self.pending) >= 32:
                raise ConversationError("sync_queue_full", "同步队列繁忙，请稍后重试。", 503)
            tid = task.get("task_id") or task.get("refresh_id") or task["scan_id"]
            self.store.job(tid, task, kind)
            self.pending[key] = tid
            try:
                self.executor.submit(self.run, key, tid, task, kind, work, time.monotonic(), completed)
            except Exception:
                self.pending.pop(key, None)
                raise
            return dict(task)

    def run(self, key, tid, task, kind, work, queued_at, completed=None):
        token = QUEUE_WAIT.set((time.monotonic()-queued_at)*1000)
        task = dict(task, status="running")
        try:
            self.store.job(tid, task, kind)
            result = work()
            task.update(result or {})
            task["status"] = "completed" if not task.get("error") else "failed"
        except (ProbeError, ConversationError) as exc:
            task.update(status="failed", error=exc.code if isinstance(exc, ConversationError) else str(exc))
        except Exception:
            task.update(status="failed", error="sync_failed")
        finally:
            task["completed_at"] = now()
            try:
                self.store.job(tid, task, kind)
                if task.get("account_id"):
                    self.store.notify(dict(type="task", account_id=task["account_id"], task_id=tid, status=task["status"]))
            finally:
                with self.lock:
                    self.pending.pop(key, None)
                QUEUE_WAIT.reset(token)
                if completed:
                    completed(task)

    def query(self, tid, aid=None):
        task = self.store.job(tid)
        if not task or (aid is not None and task.get("account_id") != aid):
            raise ConversationError("task_not_found", "任务不存在或已过期。", 404)
        return task

    def close(self):
        self.closed = True
        self.executor.shutdown(wait=True, cancel_futures=False)


class Discovery:
    def __init__(self, bridge, probe):
        self.bridge, self.probe = bridge, probe
        self.live_accounts = {}
        self.latest = None
        self.lock = threading.RLock()

    def start(self):
        task = dict(scan_id=uuid4().hex, status="running", started_at=now(), error=None)
        return ScanTask.model_validate(self.bridge.queue.submit("scan", task, "scan", self.scan))

    def scan(self, automatic=False):
        started = time.monotonic()
        try:
            result = browser_values(self.probe())
            # Validate before persisting any result from a reader.
            checked = AccountSnapshot.model_validate(dict(result, scan_id=uuid4().hex, scanned_at=now()))
            result = self.bridge.store.save_accounts(checked.model_dump(mode="json"))
            self.bridge.compatibility.observe(result)
            for item in result["accounts"]:
                if item.get("account_id"):
                    record_source(self.bridge.store,"accounts",{},item,aid=item["account_id"],automatic=automatic,elapsed=time.monotonic()-started)
            record_source(self.bridge.store,"accounts",{},dict(account_count=len(result["accounts"]),summary=result["summary"]),automatic=automatic,elapsed=time.monotonic()-started)
            with self.lock:
                self.live_accounts = {a["account_id"]: Account.model_validate(dict(a, source_state="online"))
                    for a in result["accounts"] if a.get("account_id") and a.get("instances")}
                self.latest = result
                self.bridge.process_signature = tuple(sorted((i["pid"],int(i["created"])) for i in result.get("client_instances",[])))
            return dict(summary=result["summary"], exit_code=result["exit_code"], error=result.get("error"))
        except Exception:
            self.bridge.store.compatibility_hold('probe_failed')
            record_source(self.bridge.store,"accounts",{},None,automatic=automatic,elapsed=time.monotonic()-started,error="probe_failed")
            from wechat_account_probe.models import failure_result
            result = failure_result("probe_failed")
            self.bridge.store.save_accounts(result)
            with self.lock:
                self.live_accounts = {}
                self.latest = dict(result, scan_id=uuid4().hex, scanned_at=now())
            return dict(summary=result["summary"], exit_code=1, error="probe_failed")

    def task(self, scan_id):
        return ScanTask.model_validate(self.bridge.queue.query(scan_id))

    def snapshot(self):
        from wechat_account_probe.models import failure_result
        with self.lock:
            value = dict(self.latest or dict(failure_result("not_scanned"), scan_id="archive", scanned_at=now()))
        values = self.bridge.store.accounts()
        for item in values:
            live = self.live_accounts.get(item["account_id"])
            item["instances"] = [i.model_dump() for i in live.instances] if live and item["source_state"] == "online" else []
        value["accounts"] = values
        value["accounts"].extend(a for a in (self.latest or {}).get("accounts", []) if not a.get("account_id"))
        if self.latest is None:
            value.update(exit_code=0, error=None)
        return AccountSnapshot.model_validate(value)

    def account(self, aid):
        value = self.bridge.store.account(aid)
        with self.lock:
            live = self.live_accounts.get(aid)
            value["instances"] = [i.model_dump() for i in live.instances] if live and value["source_state"] == "online" else []
        return Account.model_validate(value)

    def online(self, aid):
        value = self.account(aid)
        if value.source_state != "online" or not value.instances:
            raise ConversationError("source_offline", "微信当前离线，已保存历史仍可查询。")
        return value


class Settings:
    def __init__(self, store):
        self.store = store

    def get(self, wxid):
        return Exclusions.model_validate(self.store.settings(wxid))

    def save(self, wxid, value):
        return Exclusions.model_validate(self.store.settings(wxid, value.model_dump()))


class Conversations:
    def __init__(self, bridge):
        self.bridge, self.reader, self.discovery = bridge, bridge.reader, bridge.discovery
        self.settings = Settings(bridge.store)

    def account(self, aid):
        return self.discovery.account(aid)

    def start(self, aid):
        self.account(aid)
        task = dict(account_id=aid, refresh_id=uuid4().hex, status="queued", started_at=now())
        def work():
            result = self.bridge.source(aid, self.reader.read, "conversations", {}, task_id=task["refresh_id"])
            self.bridge.store.save_conversations(aid, result)
        return ConversationRefresh.model_validate(self.bridge.queue.submit(("conversations", aid), task, "conversations", work))

    def task(self, aid, tid):
        self.account(aid)
        return ConversationRefresh.model_validate(self.bridge.queue.query(tid, aid))

    def page(self, request):
        return ConversationPage.model_validate(self.bridge.store.conversations(request.account_id, request.cursor, request.page_size))

    def save_settings(self, aid, value):
        result = self.settings.save(self.account(aid).wxid, value)
        self.bridge.store.notify(dict(type="changed", account_id=aid, resources=["conversations", "settings"], revision=self.account(aid).revision))
        return result

    def resolve(self, aid, cid):
        row = self.bridge.store.conversation(aid, cid)
        if json.loads(row["payload"])["is_container"]:
            raise ConversationError("conversation_is_container", "分类入口暂不展开。")
        return self.account(aid), row["source_id"]


class Messages:
    def __init__(self, bridge, reader=None):
        self.bridge, self.reader = bridge, reader or bridge.reader

    def page(self, request):
        self.bridge.conversations.resolve(request.account_id, request.conversation_id)
        return MessagePage.model_validate(self.bridge.store.messages(request.account_id, request.conversation_id, request.cursor, request.page_size))

    def image(self, request):
        return MessageImage.model_validate(self.bridge.store.image(request.account_id, request.conversation_id, request.message_id))

    def start(self, request, kind="history", automatic=False):
        aid, cid = request.account_id, request.conversation_id
        _, source_id = self.bridge.conversations.resolve(aid, cid)
        task = dict(account_id=aid, conversation_id=cid, task_id=uuid4().hex, kind=kind,
                    status="queued", started_at=now())
        if kind == "history":
            body = self.bridge.store.history_body(aid, cid, request.cursor, request.page_size)
        else:
            body = dict(source_id=source_id, reference=self.bridge.store.reference(aid, cid, request.message_id))
        def work():
            if kind == "history":
                cached = self.page(request)
                if cached.needs_sync or request.refresh:
                    with self.bridge.store.source_snapshot(aid) as read_revision:
                        result = self.bridge.source(aid, lambda a: self.reader.messages(a, body), "messages", body, cid=cid, task_id=task["task_id"])
                        self.bridge.store.save_history(aid, cid, result, body, read_revision=read_revision)
                else:
                    record_source(self.bridge.store,"messages",body,cached.model_dump(mode="json"),aid=aid,cid=cid,task_id=task["task_id"],source="cache")
                return dict(page=self.page(request).model_dump(mode="json"))
            cached = self.image(request)
            if cached.needs_sync:
                result = self.bridge.source(aid, lambda a: self.reader.message_image(a, body), "image", {**body,"message_id":request.message_id}, cid=cid, automatic=automatic, task_id=task["task_id"])
                if result["status"] == "available":
                    self.bridge.store.image(aid, cid, request.message_id, result)
                else:
                    return dict(image=dict(message_id=request.message_id, **result))
            else:
                record_source(self.bridge.store,"image",dict(message_id=request.message_id),cached.model_dump(mode="json"),aid=aid,cid=cid,automatic=automatic,task_id=task["task_id"],source="cache")
            return dict(image=self.image(request).model_dump(mode="json"))
        key = (kind, aid, cid, dump(body), getattr(request, "refresh", False))
        return MessageReadTask.model_validate(self.bridge.queue.submit(key, task, kind, work))

    def query(self, aid, tid):
        self.bridge.store.account(aid)
        task=self.bridge.queue.query(tid, aid)
        if task.get("image") and task["image"]["status"]=="available":
            task["image"]=self.bridge.store.image(aid,task["conversation_id"],task["image"]["message_id"])
        return MessageReadTask.model_validate(task)


class Bridge:
    def __init__(self, store, probe, reader, *, message_reader=None, monitor=False, interval=10, compatibility_enabled=False):
        self.store, self.reader, self.interval = store, reader, interval
        self.queue = WorkQueue(store)
        self.realtime_queue = WorkQueue(store, workers=3, name="source-realtime")
        self.discovery = Discovery(self, probe)
        from .compatibility import Compatibility
        self.compatibility = Compatibility(self, compatibility_enabled)
        self.conversations = Conversations(self)
        self.messages = Messages(self, message_reader)
        self.stopped = threading.Event()
        self.thread = None
        self.process_signature = None
        self.failures = {}
        from .sync_scheduler import SyncScheduler
        self.scheduler = SyncScheduler(self)
        if monitor:
            self.discovery.start()
            self.thread = threading.Thread(target=self.loop, daemon=True, name="change-timer")
            self.thread.start()

    def source(self, aid, operation, kind="read", inputs=None, *, cid=None, automatic=False, task_id=None):
        started = time.monotonic()
        try:
            account = self.discovery.online(aid)
            result = operation(account)
            if self.discovery.online(aid).instances != account.instances:
                raise ProbeError("account_changed_during_read")
            self.store.sync_error(aid, None)
            record_source(self.store,kind,inputs or {},result,aid=aid,cid=cid,automatic=automatic,task_id=task_id,elapsed=time.monotonic()-started)
            return result
        except ProbeError as exc:
            record_source(self.store,kind,inputs or {},None,aid=aid,cid=cid,automatic=automatic,task_id=task_id,elapsed=time.monotonic()-started,error=str(exc))
            self.store.sync_error(aid, str(exc))
            if str(exc) in IDENTITY_ERRORS:
                if self.compatibility.enabled:
                    self.store.compatibility_pause(aid, str(exc))
                with self.discovery.lock:
                    self.discovery.live_accounts.pop(aid, None)
                self.store.set_state(aid, "offline")
                self.process_signature = None
            raise
        except Exception as exc:
            record_source(self.store,kind,inputs or {},None,aid=aid,cid=cid,automatic=automatic,task_id=task_id,elapsed=time.monotonic()-started,error=exc.code if isinstance(exc,ConversationError) else "source_read_failed")
            raise

    def tick(self, check_processes=True):
        if check_processes:
            from wechat_account_probe.native import enumerate_instances
            instances, _ = enumerate_instances()
            signature = tuple(sorted((i.pid, i.created) for i in instances))
            if signature != self.process_signature:
                self.discovery.scan(automatic=True)
                self.process_signature = signature
        for account in list(self.discovery.live_accounts.values()):
            if self.stopped.is_set():
                return
            self.sync_account(account.account_id)

    def sync_account(self, aid, *, lane=None, source_ids=None):
        with self.store.source_snapshot(aid) as read_revision:
            states = self.store.states(aid)
            started = time.monotonic()
            options = dict(lane=lane, source_ids=source_ids) if lane else {}
            result = self.source(aid, lambda a: self.reader.changes(a, states, **options),
                                 "changes", dict(checkpoints=states, **options), automatic=True)
            read_done = time.monotonic()
            if lane:
                result["lane"] = lane
            if result.get("conversations"):
                self.store.save_conversations(aid, result["conversations"])
            self.store.save_delta(aid, result, read_revision=read_revision)
            result["timings_ms"] = dict(queue_wait=round(QUEUE_WAIT.get(), 3), source_read=round((read_done-started)*1000, 3), archive_commit=round((time.monotonic()-read_done)*1000, 3))
            record_delta_groups(self.store, aid, states, result, time.monotonic()-started)
            return result

    def loop(self):
        self.scheduler.loop()

    def close(self):
        self.stopped.set()
        self.scheduler.wake.set()
        if self.thread:
            self.thread.join(timeout=2)
        self.realtime_queue.close()
        self.queue.close()
        self.reader.close()
        self.store.close()
