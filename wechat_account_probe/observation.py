"""Passive OUTOFCONTEXT WinEvent observer; no injected hooks or UI actions.

User activity is allowed. Events near user input are counted, not suppressed;
their timing is evidence, not reliable causal attribution to this module.
Only the current desktop can be observed, even when scanning other sessions.
"""
import ctypes as C
from ctypes import wintypes as W
import threading
import time


class Observation:
    def __init__(self, pids):
        self.pids = set(pids)
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.result = dict(available=False, scope='current_desktop_only',
                           wechat_foreground_events=0, wechat_focus_events=0,
                           events_near_user_input=0, events=[], attribution='not_inferred')

    def __enter__(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.ready.wait(timeout=2)
        return self

    def __exit__(self, *_args):
        self.stop.set()
        self.thread.join(timeout=2)

    def _run(self):
        hooks = []
        try:
            user = C.WinDLL('user32', use_last_error=True)
            kernel = C.WinDLL('kernel32', use_last_error=True)
            callback_type = C.WINFUNCTYPE(None, W.HANDLE, W.DWORD, W.HWND,
                                         W.LONG, W.LONG, W.DWORD, W.DWORD)
            user.SetWinEventHook.argtypes = [W.DWORD, W.DWORD, W.HMODULE,
                                             callback_type, W.DWORD, W.DWORD, W.DWORD]
            user.SetWinEventHook.restype = W.HANDLE
            user.UnhookWinEvent.argtypes = [W.HANDLE]
            user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
            user.GetForegroundWindow.restype = W.HWND
            user.PeekMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT]

            class LastInput(C.Structure):
                _fields_ = [('cbSize', W.UINT), ('dwTime', W.DWORD)]

            user.GetLastInputInfo.argtypes = [C.POINTER(LastInput)]

            def receive(_hook, event, hwnd, _object, _child, _thread, _time):
                pid = W.DWORD()
                user.GetWindowThreadProcessId(hwnd, C.byref(pid))
                if pid.value not in self.pids:
                    return
                field = 'wechat_foreground_events' if event == 3 else 'wechat_focus_events'
                self.result[field] += 1
                last = LastInput(C.sizeof(LastInput), 0)
                age = None
                if user.GetLastInputInfo(C.byref(last)):
                    age = (kernel.GetTickCount() - last.dwTime) & 0xffffffff
                    if age < 1000:
                        self.result['events_near_user_input'] += 1
                if len(self.result['events']) < 32:
                    self.result['events'].append(dict(event=event,
                        seconds_since_start=round(time.monotonic()-started,3),
                        input_age_ms=age,current_foreground_matches=hwnd==user.GetForegroundWindow()))

            callback = callback_type(receive)
            started = time.monotonic()
            for event in (3, 0x8005):
                # WINEVENT_OUTOFCONTEXT=0, hmod=NULL: callback stays in this process.
                hook = user.SetWinEventHook(event, event, None, callback, 0, 0, 0)
                if not hook:
                    raise OSError
                hooks.append(hook)
            self.result['available'] = True
            self.ready.set()
            message = W.MSG()
            while not self.stop.wait(0.01):
                while user.PeekMessageW(C.byref(message), None, 0, 0, 1):
                    user.TranslateMessage(C.byref(message))
                    user.DispatchMessageW(C.byref(message))
        except Exception:
            self.result['available'] = False
            self.result['reason'] = 'event_observation_unavailable'
            self.ready.set()
        finally:
            for hook in hooks:
                user.UnhookWinEvent(hook)
