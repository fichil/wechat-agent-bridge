"""Own only hidden child workers; serialize expensive reads across accounts."""
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
    def __init__(self):
        self._lock = threading.Lock()
        self._workers = {}
        self._closed = threading.Event()
        self._reaper = threading.Thread(target=self._reap, daemon=True, name="conversation-idle-cleanup")
        self._reaper.start()

    def _reap(self):
        while not self._closed.wait(5):
            self._reap_idle()

    def _reap_idle(self):
        if not self._lock.acquire(blocking=False):
            return
        try:
            for key, worker in list(self._workers.items()):
                if worker.process.poll() is not None or time.monotonic() - worker.used_at >= 60:
                    worker.close()
                    del self._workers[key]
        finally:
            self._lock.release()

    def _call(self, account, command, data=None):
        heavy = command in ("read", "messages", "message_image", "changes")
        timeout = 180 if command in ("messages", "changes") else 60 if heavy else 10
        # A cached list must not block behind a cold scan or bypass identity validation.
        if not self._lock.acquire(timeout=timeout if heavy else 0.1):
            raise ProbeError("conversation_busy")
        worker = None
        try:
            if self._closed.is_set():
                raise ProbeError("service_stopping")
            if not account.instances or not account.wxid:
                raise ProbeError("account_not_found")
            # Deterministic instance binding, never silently switch instances on failure.
            source = sorted(account.instances, key=lambda i: (i.session_id, i.pid))[0]
            instance = Instance(source.pid, int(source.created), source.session_id, source.version)
            key = account.account_id
            worker = self._workers.get(key)
            if worker and (worker.instance != instance or worker.wxid != account.wxid or
                           worker.process.poll() is not None):
                worker.close()
                del self._workers[key]
                worker = None
            if worker is None:
                worker = Worker(instance, account.wxid)
                self._workers[key] = worker
            return worker.call(command, timeout, data) if data is not None else worker.call(command, timeout)
        except Exception:
            if worker:
                worker.close()
                self._workers.pop(account.account_id, None)
            raise
        finally:
            self._lock.release()

    def read(self, account):
        return self._call(account, "read")

    def validate(self, account):
        self._call(account, "validate")

    def messages(self, account, request):
        return self._call(account, "messages", request)

    def message_image(self, account, request):
        return self._call(account, "message_image", request)

    def changes(self, account, states):
        return self._call(account, "changes", {"states": states})

    def close(self):
        self._closed.set()
        with self._lock:
            for worker in self._workers.values():
                worker.close()
            self._workers.clear()
        self._reaper.join(timeout=2)
