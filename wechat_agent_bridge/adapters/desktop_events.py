"""Out-of-context WinEvent observation; never injects input or changes focus."""

import threading
import time

from ..console_models import ConsoleError


class EventEvidence:
    def __init__(self):
        self.phase = "initializing"
        self.events = []
        self.failed = False
        self.lock = threading.Lock()

    def record(self, event, target, *, thread_id=0, event_ms=0):
        with self.lock:
            if len(self.events) >= 128:
                self.failed = True
                return
            self.events.append({"time": time.monotonic(), "phase": self.phase,
                                "event": event, "target_process": bool(target),
                                "source_thread": int(thread_id), "system_event_ms": int(event_ms)})

    def check(self):
        with self.lock:
            if self.failed:
                raise ConsoleError("desktop_event_observation_failed")
            if self.events:
                raise ConsoleError("desktop_event_change_observed")


class DesktopEventObserver:
    """Own message-pump thread, outside the client process. No window titles."""
    def __init__(self, target_pid):
        self.target_pid = target_pid
        self.evidence = EventEvidence()
        self.ready, self.stop = threading.Event(), threading.Event()
        self.barrier = threading.Event()
        self.thread = None

    def __enter__(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self.ready.wait(3) or self.evidence.failed:
            self.close()
            raise ConsoleError("desktop_event_observation_failed")
        return self

    def check(self):
        if self.stop.is_set() or not self.thread or not self.thread.is_alive():
            raise ConsoleError("desktop_event_observation_failed")
        self.barrier.clear()
        if not self.barrier.wait(1):
            raise ConsoleError("desktop_event_observation_failed")
        self.evidence.check()

    def set_phase(self, phase):
        with self.evidence.lock:
            self.evidence.phase = phase

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(3)
            if self.thread.is_alive():
                self.evidence.failed = True

    def __exit__(self, *args):
        self.close()
        if self.evidence.failed and args[0] is None:
            raise ConsoleError("desktop_event_observation_failed")

    def _run(self):
        hooks = []
        try:
            import ctypes as c
            from ctypes import wintypes as w
            u = c.WinDLL("user32", use_last_error=True)
            callback_type = c.WINFUNCTYPE(None, w.HANDLE, w.DWORD, w.HWND, w.LONG, w.LONG, w.DWORD, w.DWORD)
            u.SetWinEventHook.argtypes = [w.DWORD, w.DWORD, w.HMODULE, callback_type, w.DWORD, w.DWORD, w.DWORD]
            u.SetWinEventHook.restype = w.HANDLE
            u.UnhookWinEvent.argtypes = [w.HANDLE]
            u.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
            u.PeekMessageW.argtypes = [c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
            u.TranslateMessage.argtypes = [c.POINTER(w.MSG)]
            u.DispatchMessageW.argtypes = [c.POINTER(w.MSG)]
            u.DispatchMessageW.restype = c.c_ssize_t

            def receive(hook, event, hwnd, obj, child, thread, milliseconds):
                try:
                    pid = w.DWORD()
                    u.GetWindowThreadProcessId(hwnd, c.byref(pid))
                    self.evidence.record("foreground" if event == 3 else "focus", pid.value == self.target_pid,
                                         thread_id=thread, event_ms=milliseconds)
                except Exception:
                    self.evidence.failed = True
            callback = callback_type(receive)
            for event in (3, 0x8005):  # FOREGROUND, OBJECT_FOCUS; OUTOFCONTEXT=0.
                hook = u.SetWinEventHook(event, event, None, callback, 0, 0, 0)
                if not hook:
                    raise OSError("hook_failed")
                hooks.append(hook)
            self.ready.set()
            msg = w.MSG()
            while not self.stop.is_set():
                while u.PeekMessageW(c.byref(msg), None, 0, 0, 1):
                    u.TranslateMessage(c.byref(msg))
                    u.DispatchMessageW(c.byref(msg))
                self.barrier.set()
                self.stop.wait(0.01)
        except Exception:
            self.evidence.failed = True
            self.ready.set()
        finally:
            for hook in hooks:
                if not u.UnhookWinEvent(hook):
                    self.evidence.failed = True
