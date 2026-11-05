"""Bounded background model work, independent of source-reading and SQL workers."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from uuid import uuid4
from .ai_provider import Provider, normalize_image, build_request
from .ai_execution_storage import stamp
from .conversations import ConversationError
from .message_models import ImageRequest
from . import secret_protection
from .diagnostics import log_context

class AiExecution:
    def __init__(self, store, bridge, provider=None, automatic=True):
        self.store, self.bridge = store, bridge
        self.provider = provider or Provider()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai-request")
        self.lock = threading.RLock()
        self.pending = set()
        self.update_draining = False
        self.conversation_locks = {}
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.image_attempts = {}
        self.thread = None
        if automatic: self.start_automatic()

    def start_automatic(self):
        if self.thread is None and not self.stopped.is_set():
            self.thread = threading.Thread(target=self.loop, name="ai-scheduler", daemon=True)
            self.thread.start()

    def submit(self, key, work):
        with self.lock:
            if self.stopped.is_set() or self.update_draining:
                raise ConversationError("service_stopping", "服务正在停止。", 503)
            if key in self.pending:
                return
            self.pending.add(key)
        def run():
            try:
                work()
            finally:
                with self.lock:
                    self.pending.discard(key)
                self.wake.set()
        self.executor.submit(run)

    def start_profile(self, fields=None, key=None, identity=None, expected=None):
        save_key = key
        if identity:
            if key is None:
                saved, key = self.store.ai_secret(identity, expected)
            else:
                saved = self.store.api_profile(identity)
                if saved["revision"] != expected:
                    raise ConversationError("revision_conflict", "配置已在其他位置更新，请重新加载。", 409)
            if fields is None:
                fields = saved
        operation = "test" if fields and "profile_id" in fields else "update" if identity else "create"
        # Fail DPAPI before any network call. Candidate keys are never persisted as job inputs.
        secret_protection.protect(key)
        task = dict(task_id=uuid4().hex, operation=operation, status="queued", profile_id=identity, started_at=stamp())
        self.store.api_task(task["task_id"], task)
        def work():
            task["status"] = "running"
            self.store.api_task(task["task_id"], task)
            try:
                settings = self.store.ai_settings()
                with log_context(self.store, task_id=task["task_id"], trigger=operation, profile_id=identity,
                                 profile_revision=expected if operation=="test" else (expected or 0)+1, settings_revision=settings["revision"]):
                    verification = self.provider.test(fields, key, settings["instructions"])
                if operation == "test":
                    self.store.save_verification(identity, expected, verification, task)
                else:
                    profile = self.store.save_api_profile(fields, save_key, identity, expected, verification, task)
                    task["profile_id"] = profile["profile_id"]
                task.update(status="completed", verification=verification)
            except ConversationError as exc:
                task.update(status="failed", error=exc.code, message=exc.message)
                if operation == "test":
                    try:
                        self.store.save_verification(identity, expected, dict(status="failed", vision="unverified", output_mode="schema", tested_at=stamp(), error=exc.code, message=exc.message))
                    except ConversationError:
                        pass
            except Exception:
                task.update(status="failed", error="ai_test_failed", message="测试无法完成，配置未保存。")
            task["completed_at"] = stamp()
            self.store.api_task(task["task_id"], task)
            self.store.log_application(task["task_id"], task)
        self.submit("profile:"+task["task_id"], work)
        return task.copy()

    def start(self, aid, cid, request_id, trigger="manual"):
        self.bridge.compatibility.guard(aid)
        run = self.store.start_ai_run(aid, cid, request_id, trigger)
        if run["status"] == "queued":
            self.submit("run:"+run["run_id"], lambda: self.perform(aid, cid, run["run_id"]))
        return run

    def retry(self, aid, cid, rid, request_id):
        run = self.store.ai_run(aid, cid, rid)
        if run["status"] not in ("failed", "conflicted", "cancelled"):
            raise ConversationError("ai_retry_invalid", "只有失败、中断或取消的任务可以重试。", 409)
        return self.start(aid, cid, request_id, "retry")

    def request_images(self, aid, cid):
        with self.lock:
            current = time.monotonic()
            self.image_attempts = {mid:at for mid,at in self.image_attempts.items() if current-at < 60}
            mids = self.store.ai_missing_images(aid, cid, exclude=tuple(self.image_attempts))
            for mid in mids:
                self.image_attempts[mid] = current
        for mid in mids:
            try:
                self.bridge.messages.start(ImageRequest(account_id=aid, conversation_id=cid, message_id=mid), "image", automatic=True)
            except ConversationError:
                pass

    def perform(self, aid, cid, rid):
        with self.lock:
            mutex = self.conversation_locks.setdefault((aid, cid), threading.Lock())
        with mutex:
            self._perform(aid, cid, rid)

    def _perform(self, aid, cid, rid):
        try:
            self.bridge.compatibility.guard(aid)
            if self.store.ai_run(aid, cid, rid).get('processing_type') != 'realtime_reply':
                self.request_images(aid, cid)
            snapshot = self.store.claim_ai_run(aid, cid, rid)
            if snapshot is None:
                return
            profile, envelope = snapshot["profile"], snapshot["envelope"]
            encrypted_key = snapshot.pop("encrypted_key")
            for item in envelope["messages"]:
                content = item["content"]
                if "_image_bytes" in content:
                    content["image"] = normalize_image(content.pop("_image_bytes"), content.pop("_image_mime"))
            mode = profile["verification"]["output_mode"]
            if not snapshot.get('resumed'):
                caps = dict(image=True, sticker=True)  # Isolated adapters never operate real WeChat.
                if self.bridge.compatibility.enabled:
                    native = self.bridge.compatibility.sender.capability(aid)
                    caps = dict(image=native.get('send_image',False),sticker=native.get('send_sticker',False))
                envelope['reply_media'] = self.store.prepare_reply_candidates(aid,cid,rid,caps)
            while True:
                try:
                    if not snapshot.get('resumed'):
                        envelope.update(self.store.preview_ai_reply_envelope(
                            aid, cid, rid, {m['message_id'] for m in envelope['messages']}))
                    # Request sizing does not need credentials; persist the input before decrypting.
                    build_request(profile, "", envelope, mode)
                    break
                except ConversationError as exc:
                    if snapshot.get('resumed') or exc.code != "ai_input_too_large" or len(envelope["messages"]) <= 1:
                        raise
                    if envelope.get('processing_type') == 'realtime_reply':
                        targets = set(envelope['reply_target_ids'])
                        optional = next((m for m in envelope['messages'] if m['message_id'] not in targets), None)
                        removed = optional or envelope['messages'][-1]
                        envelope['messages'].remove(removed)
                    else:
                        envelope["messages"].pop()
            if not snapshot.get('resumed'):
                self.store.trim_ai_run(aid, cid, rid, {m["message_id"] for m in envelope["messages"]})
            reply_ids = self.store.ai_run(aid,cid,rid).get('reply_trigger_ids',[])
            if not snapshot.get('resumed'):
                envelope.update(self.store.ai_reply_envelope(aid,cid,rid))
            # Validate the final permissions/targets, not an earlier batch projection.
            build_request(profile, "", envelope, mode)
            if not self.store.save_ai_snapshot(aid,cid,rid,profile,envelope): return
            # Closing AI while preparing must not launch a new remote request.
            self.bridge.compatibility.guard(aid)
            key = secret_protection.reveal(encrypted_key)
            if not self.store.mark_ai_attempt(aid, cid, rid):
                return
            run = self.store.ai_run(aid, cid, rid)
            trigger_at = self.store.call(lambda: self.store.db.execute("SELECT last_trigger_at FROM delivery_sessions WHERE account_id=? AND conversation_id=?", (aid,cid)).fetchone())
            timings = run.get('timings_ms', {}) or (dict(trigger_to_request=max(0, round((time.time()-trigger_at[0])*1000, 3))) if reply_ids and trigger_at and trigger_at[0] else {})
            request_started = time.monotonic()
            with log_context(self.store, timings_ms=timings, account_id=aid, conversation_id=cid, task_id=rid,
                processing_type=run.get('processing_type'),
                attempt_count=run.get('attempt_count'), max_retries=run.get('max_retries'),
                profile_id=profile['profile_id'], profile_revision=profile['revision'],
                operation="conversation", trigger=run["trigger"], memory_revision=run["memory_revision"],
                role_revision=run["role_revision"], settings_revision=run["settings_revision"]):
                result = self.provider.generate(profile, key, envelope, mode)
            self.store.finish_ai_run(aid, cid, rid, result=result, model_duration_ms=round((time.monotonic()-request_started)*1000, 3))
        except ConversationError as exc:
            if exc.code != 'compatibility_paused':
                self.store.finish_ai_run(aid, cid, rid, error=exc)
            else:
                self.store.defer_ai_run(rid)
        except Exception:
            self.store.finish_ai_run(aid, cid, rid, error=ConversationError("ai_execution_failed", "AI 处理未完成，记忆未更新，请手动重试。", 503))
        finally:
            try: self.store.log_application(rid, self.store.ai_run(aid, cid, rid))
            except ConversationError: pass  # Account/run may have been deleted while HTTP was in flight.

    def tick(self):
        if self.update_draining: return
        retries = self.store.ai_retry_candidates()  # Also yields queued memory work to new mentions.
        candidates = self.store.ai_candidates()
        work = [(bool(r.get('reply_trigger_ids')) and r.get('processing_type') != 'memory_backfill', 'retry', r) for r in retries]
        work += [(bool(c.get('reply_priority')), 'new', c) for c in candidates]
        for _, kind, item in sorted(work, key=lambda entry: not entry[0]):
            with self.lock:
                if len(self.pending) >= 2 or self.stopped.is_set(): break
            try:
                if kind == 'retry':
                    self.submit('run:'+item['run_id'], lambda r=item: self.perform(r['account_id'],r['conversation_id'],r['run_id']))
                else:
                    self.start(item['account_id'], item['conversation_id'], uuid4().hex, 'auto')
            except ConversationError:
                pass
        for candidate in self.store.ai_enabled_sessions():
            if self.stopped.is_set():
                return
            self.request_images(candidate["account_id"], candidate["conversation_id"])

    def notify(self, event):
        if set(event.get("resources", ())) & {"compatibility", "messages", "conversation_preferences", "ai_runs", "api_profiles", "ai_defaults", "role_cards", "ai_settings"}:
            self.wake.set()

    def loop(self):
        while not self.stopped.is_set():
            self.wake.clear()
            delay = 3
            try:
                self.tick()
                delay = self.store.ai_schedule_delay()
            except Exception:
                pass  # No secret-bearing exception objects go to logs.
            self.wake.wait(delay)

    def close(self):
        self.stopped.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=4)
        self.executor.shutdown(wait=True)
