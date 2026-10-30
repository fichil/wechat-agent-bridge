"""Own only hidden child workers; isolate expensive reads by account and lane."""
from dataclasses import asdict
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from wechat_account_probe.models import Instance, ProbeError

ROOT = Path(__file__).resolve().parents[1]
MAX_RESPONSE = 48 * 1024 * 1024


class Worker:
    def __init__(self, instance, wxid):
        from scripts.windows_job import WindowsJob
        self.instance, self.wxid = instance, wxid
        self.process = None
        self.job = WindowsJob()
        self.responses = queue.Queue(maxsize=2)
        self.used_at = time.monotonic()
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-B", "-X", "utf8", "-m", "wechat_conversations.worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=ROOT, creationflags=0x08000000 | 0x40)
            # The child blocks for a request until it belongs to our kill-on-close job.
            self.job.assign(self.process)
            threading.Thread(target=self._receive, daemon=True).start()
        except Exception:
            self.close()
            raise

    def _receive(self):
        try:
            while True:
                line = self.process.stdout.readline(MAX_RESPONSE + 1)
                if not line or len(line) > MAX_RESPONSE or not line.endswith(b"\n"):
                    self.responses.put(None)
                    return
                self.responses.put(json.loads(line))
        except (ValueError, OSError):
            self.responses.put(None)

    def call(self, command, timeout, data=None):
        self.used_at = time.monotonic()
        payload = dict(command=command, instance=asdict(self.instance), wxid=self.wxid, timeout=timeout)
        if data is not None:
            payload["data"] = data
        try:
            self.process.stdin.write(json.dumps(payload).encode() + b"\n")
            self.process.stdin.flush()
            response = self.responses.get(timeout=timeout)
        except queue.Empty:
            raise ProbeError("conversation_timeout") from None
        except (OSError, ValueError):
            raise ProbeError("conversation_worker_disconnected") from None
        finally:
            self.used_at = time.monotonic()
        if not isinstance(response, dict):
            raise ProbeError("conversation_worker_disconnected")
        if not response.get("ok"):
            raise ProbeError(response.get("error", "conversation_read_failed"))
        return response["result"]

    def close(self):
        import psutil
        descendants = []
        if self.process and self.process.poll() is None:
            try:
                descendants = psutil.Process(self.process.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                pass
        self.job.close()
        if self.process:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=2)
            for stream in (self.process.stdin, self.process.stdout):
                if stream:
                    stream.close()
        # Windows can signal the venv launcher before its interpreter descendants finish.
        # Wait for the complete owned tree rather than reporting cleanup from only the launcher.
        if descendants:
            _, alive = psutil.wait_procs(descendants, timeout=2)
            if alive:
                raise ProbeError("conversation_cleanup_failed")


class LocalConversationReader:
    LIMITS = {"realtime": 3, "background": 1}

    def __init__(self):
        self._condition = threading.Condition()
        self._workers = {}
        self._busy = set()
        self._closed = threading.Event()
        self._reaper = threading.Thread(target=self._reap, daemon=True, name="conversation-idle-cleanup")
        self._reaper.start()

    def _reap(self):
        while not self._closed.wait(5):
            self._reap_idle()

    def _reap_idle(self):
        retired = []
        with self._condition:
            for key, worker in list(self._workers.items()):
                if key not in self._busy and (worker.process.poll() is not None or time.monotonic()-worker.used_at >= 60):
                    self._busy.add(key)
                    retired.append((key, worker))
        for key, worker in retired:
            try:
                worker.close()
            finally:
                with self._condition:
                    self._workers.pop(key, None)
                    self._busy.discard(key)
                    self._condition.notify_all()

    def _reserve(self, key, deadline):
        retired = None
        with self._condition:
            while True:
                if self._closed.is_set():
                    raise ProbeError("service_stopping")
                occupied = set(self._workers) | self._busy
                lane_keys = [k for k in occupied if k[1] == key[1]]
                if key not in self._busy:
                    if key in self._workers or len(lane_keys) < self.LIMITS[key[1]]:
                        break
                    idle = [k for k in lane_keys if k not in self._busy]
                    if idle:
                        victim = min(idle, key=lambda k: self._workers[k].used_at)
                        retired = self._workers.pop(victim)
                        break
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise ProbeError("conversation_busy")
                self._condition.wait(remaining)
            self._busy.add(key)
            worker = self._workers.get(key)
        return worker, retired

    def _call(self, account, command, data=None, *, lane="background"):
        if lane not in self.LIMITS:
            raise ProbeError("worker_protocol_invalid")
        heavy = command in ("read", "messages", "message_image", "changes")
        timeout = 180 if command in ("messages", "changes") else 60 if heavy else 10
        if not account.instances or not account.wxid:
            raise ProbeError("account_not_found")
        source = sorted(account.instances, key=lambda i: (i.session_id, i.pid))[0]
        instance = Instance(source.pid, int(source.created), source.session_id, source.version,
                            source.core_sha256, source.profile_id)
        key = account.account_id, lane
        worker, retired = self._reserve(key, time.monotonic()+(timeout if heavy else .1))
        try:
            if retired:
                retired.close()
            if worker and (worker.instance != instance or worker.wxid != account.wxid or worker.process.poll() is not None):
                worker.close()
                with self._condition:
                    self._workers.pop(key, None)
                worker = None
            if worker is None:
                worker = Worker(instance, account.wxid)
                with self._condition:
                    self._workers[key] = worker
            return worker.call(command, timeout, data) if data is not None else worker.call(command, timeout)
        except Exception:
            if worker:
                with self._condition:
                    self._workers.pop(key, None)
                worker.close()
            raise
        finally:
            with self._condition:
                self._busy.discard(key)
                self._condition.notify_all()

    def read(self, account):
        return self._call(account, "read")

    def validate(self, account):
        self._call(account, "validate")

    def messages(self, account, request):
        return self._call(account, "messages", request)

    def message_image(self, account, request):
        return self._call(account, "message_image", request)

    def changes(self, account, states, *, lane=None, source_ids=None):
        request = {"states": states}
        if lane:
            request.update(lane=lane, source_ids=source_ids or [])
        return self._call(account, "changes", request, lane=lane or "background")

    def close(self):
        self._closed.set()
        with self._condition:
            self._condition.notify_all()
            while self._busy:
                self._condition.wait()
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            worker.close()
        self._reaper.join(timeout=2)
