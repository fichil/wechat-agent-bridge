"""Bounded, cancelable enrollment; no messaging or provider capability."""
import threading
import time
from .console_models import ConsoleError


class IdentityProbe:
    def __init__(self, instance_id, observe, restore, commit, check, *, timeout=60,
                 clock=time.monotonic, wait=None):
        if type(timeout) not in (int,float) or not 1 <= timeout <= 60:
            raise ConsoleError('identity_probe_invalid_timeout')
        self.instance_id=instance_id
        self.observe,self.restore,self.commit,self.check=observe,restore,commit,check
        self.timeout,self.clock=timeout,clock
        self.cancel_event=threading.Event()
        self.wait=wait or self.cancel_event.wait
        self.lock=threading.RLock()
        self.state='waiting'
        self.reason=None
        self.restored=False
        self.identity_saved=False
        self.thread=None

    def status(self):
        with self.lock:
            return dict(state=self.state,instance_id=self.instance_id,reason=self.reason,
                        restored=self.restored,identity_saved=self.identity_saved)

    def cancel(self):
        with self.lock:self.cancel_event.set()

    def running(self):
        return self.state in ('waiting','verifying','restoring')

    def run(self):
        deadline=self.clock()+self.timeout
        account=None
        failure=None
        def checkpoint():
            if self.cancel_event.is_set():raise ConsoleError('identity_probe_cancelled')
            if self.clock()>=deadline:raise ConsoleError('identity_probe_timeout')
            self.check()
        try:
            while True:
                checkpoint()
                try:
                    self.state='verifying'
                    account=self.observe()
                    checkpoint()
                    break
                except ConsoleError as exc:
                    if str(exc)!='account_identity_uncertain_open_own_profile':raise
                    self.state='waiting'
                    self.wait(.2)
        except Exception as exc:
            failure=str(exc) if isinstance(exc,ConsoleError) else 'identity_probe_failed'
        finally:
            self.state='restoring'
            try:
                self.restore()
                self.restored=True
            except Exception:
                failure='identity_probe_restore_failed'
        with self.lock:
            if failure is None:
                try:
                    checkpoint()
                    self.commit(account)
                    self.identity_saved=True
                except Exception as exc:
                    failure=str(exc) if isinstance(exc,ConsoleError) else 'identity_probe_commit_failed'
            self.reason=failure
            self.state='verified' if failure is None else ('cancelled' if failure=='identity_probe_cancelled' else 'failed')
        return self.status()
