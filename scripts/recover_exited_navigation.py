"""Retire a terminal, text-free selection after its exact process instance ends.

Default disabled; inspection never changes the journal. Recovery records exit
evidence before releasing the original lock, then clears only that pending
selection. It never repairs a live process or authorizes a replacement instance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import probe_background_draft as original
from scripts.inspect_bound_search import require_terminal_selection
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.console_store import digest

OTHER_PENDING = ('patch', 'thread_hook', 'draft_probe', 'development_window_restore', 'development_exact_search')


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def require_gone(fp, observation):
    """A missing handle alone, an access error or a timeout proves no exit."""
    pid, created = fp.get('pid'), fp.get('created')
    if (type(pid) is not int or not 4 < pid <= 0xffffffff or
            type(created) is not int or not 0 < created < 2**64 or
            not isinstance(observation, dict) or observation.get('pid') != pid):
        raise ConsoleError('navigation_exit_process_identity_required')
    if 'open_error' in observation:
        if (observation.get('open_error') != 87 or observation.get('pid_enumeration_complete') is not True or
                observation.get('pid_in_enumeration') is not False or 'created' in observation):
            raise ConsoleError('navigation_exit_process_absence_unproven')
        return 'pid_absent'
    actual = observation.get('created')
    wait = observation.get('wait_result')
    if type(actual) is not int or actual < created or type(wait) is not int or wait not in (0, 258):
        raise ConsoleError('navigation_exit_process_observation_invalid')
    if actual > created:
        return 'pid_reused'
    if wait == 0:
        return 'terminated_handle'
    raise ConsoleError('navigation_exit_original_process_still_alive')


class WindowsProcessObserver:
    """Only QUERY_LIMITED_INFORMATION | SYNCHRONIZE; no VM or write rights."""
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        self.k = c.WinDLL('kernel32', use_last_error=True)
        for name, args, result in (
            ('OpenProcess', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            ('CloseHandle', [w.HANDLE], w.BOOL),
            ('GetProcessTimes', [w.HANDLE] + [c.POINTER(w.FILETIME)] * 4, w.BOOL),
            ('WaitForSingleObject', [w.HANDLE, w.DWORD], w.DWORD),
            ('K32EnumProcesses', [c.POINTER(w.DWORD), w.DWORD, c.POINTER(w.DWORD)], w.BOOL),
        ):
            method = getattr(self.k, name)
            method.argtypes, method.restype = args, result

    def inspect(self, pid):
        if type(pid) is not int or not 4 < pid <= 0xffffffff:
            raise ConsoleError('navigation_exit_pid_invalid')
        c, w, k = self.c, self.w, self.k
        handle = k.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            error = c.get_last_error()
            result = dict(pid=pid, open_error=error)
            if error == 87:
                pids, used = (w.DWORD * 65536)(), w.DWORD()
                if not k.K32EnumProcesses(pids, c.sizeof(pids), c.byref(used)):
                    raise ConsoleError('navigation_exit_pid_enumeration_failed')
                if used.value >= c.sizeof(pids) or used.value % c.sizeof(w.DWORD):
                    raise ConsoleError('navigation_exit_pid_enumeration_incomplete')
                result.update(pid_enumeration_complete=True,
                              pid_in_enumeration=pid in pids[:used.value // c.sizeof(w.DWORD)])
            return result
        try:
            times = [w.FILETIME() for _ in range(4)]
            if not k.GetProcessTimes(handle, *(c.byref(t) for t in times)):
                raise ConsoleError('navigation_exit_creation_time_unavailable')
            return dict(pid=pid, created=(times[0].dwHighDateTime << 32) | times[0].dwLowDateTime,
                        wait_result=int(k.WaitForSingleObject(handle, 0)))
        finally:
            if not k.CloseHandle(handle):
                raise ConsoleError('navigation_exit_process_handle_close_failed')


def lock_identity(path, token):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400 or
            info.st_size != 32 or path.read_text(encoding='ascii') != token):
        raise ConsoleError('navigation_exit_original_lock_changed')
    return info.st_dev, info.st_ino


def recover(store, lock, account, observer, *, apply=False, checkpoint=lambda: None):
    binding = store.get('binding', 'account', {})
    pending = store.get('development_navigation', 'pending')
    if not isinstance(pending, dict):
        raise ConsoleError('navigation_exit_terminal_pending_required')
    fp = binding.get('fingerprint', {})
    identity = binding.get('account', {})
    if (identity.get('wechat_id') != account or
            identity.get('identity_source') != 'uia_own_profile_exact_id_user_selected_window'):
        raise ConsoleError('navigation_exit_explicit_bound_account_required')
    request = pending.get('request', '')
    attempt = store.get('development_navigation', request)
    token = pending.get('lock_token')
    require_terminal_selection(pending, attempt, fp, token)
    key = request + ':process-exit-observed'
    expected = dict(kind='navigation_process_exit_observation', fingerprint=fp,
                    pending=pending, original_attempt_sha256=content_hash(attempt))
    previous = store.get('development_navigation', key)
    if previous is not None and (not isinstance(previous, dict) or
            any(previous.get(k) != v for k, v in expected.items()) or
            not previous.get('observations') or
            any(require_gone(fp, item) not in ('pid_absent', 'pid_reused', 'terminated_handle')
                for item in previous['observations'])):
        raise ConsoleError('navigation_exit_existing_observation_mismatch')
    original_lock = lock_identity(lock.path, token)
    if original_lock is None and previous is None:
        raise ConsoleError('navigation_exit_original_lock_missing_without_receipt')

    def check():
        checkpoint()
        if (store.get('binding', 'account') != binding or
                store.get('development_navigation', 'pending') != pending or
                store.get('development_navigation', request) != attempt):
            raise ConsoleError('navigation_exit_original_state_changed')
        if any(store.get(bucket, 'pending') for bucket in OTHER_PENDING):
            raise ConsoleError('navigation_exit_other_recovery_pending')
        now = lock_identity(lock.path, token)
        if now != original_lock:
            raise ConsoleError('navigation_exit_original_lock_changed')

    check()
    first = observer.inspect(fp['pid'])
    reason = require_gone(fp, first)
    check()
    second = observer.inspect(fp['pid'])
    require_gone(fp, second)
    result = dict(kind='exited_navigation_recovery', state='process_exit_inspected',
                  fingerprint=fp, request_id=request, exit_reason=reason,
                  observations=[first, second], original_unknown_preserved=True,
                  account_binding_modified=False, history_read=False, send_invoked=False,
                  api_called=False, process_memory_accessed=False, lock_released=False,
                  background_qualified=False)
    if not apply:
        return result
    check()
    if previous is None:
        # Durable proof exists before unlink. A crash after unlink is resumable
        # under the still-pending original request; its unknown is never changed.
        store.put('development_navigation', key, dict(**expected, observations=[first, second]))
    check()
    require_gone(fp, observer.inspect(fp['pid']))
    check()
    if original_lock is not None:
        lock.token, lock.owned = token, True
        lock.release()
    result['lock_released'] = True
    result['state'] = 'exited_navigation_retired'
    # Every other entry remains byte-for-byte intact. Pending stays present if
    # commit fails, so ordinary live paths remain blocked even after unlink.
    with store.lock:
        store.db.execute('BEGIN IMMEDIATE')
        try:
            checkpoint()
            if (store.get('binding', 'account') != binding or
                    store.get('development_navigation', 'pending') != pending or
                    store.get('development_navigation', request) != attempt or
                    lock.path.exists() or any(store.get(b, 'pending') for b in OTHER_PENDING)):
                raise ConsoleError('navigation_exit_state_changed_before_commit')
            observed = store.get('development_navigation', key)
            if not isinstance(observed, dict) or any(observed.get(k) != v for k, v in expected.items()):
                raise ConsoleError('navigation_exit_durable_observation_missing')
            for record_key, value in ((request + ':process-exit-retired', result), ('pending', None)):
                store.db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',
                                 ('development_navigation', digest(record_key), store._pack(value)))
            store.db.commit()
        except BaseException:
            store.db.rollback()
            raise
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--inspect-exited-selection', action='store_true')
    mode.add_argument('--recover-exited-selection', action='store_true')
    parser.add_argument('--account')
    args = parser.parse_args(argv)
    if not (args.inspect_exited_selection or args.recover_exited_selection):
        print(json.dumps(dict(state='disabled', send_invoked=False, api_called=False)))
        return 0
    from wechat_agent_bridge.console_store import ConsoleStore
    from wechat_agent_bridge.protection import DPAPIProtector
    store = None
    try:
        if not args.account or os.name != 'nt':
            raise ConsoleError('navigation_exit_windows_and_explicit_account_required')
        store = ConsoleStore(original.DIRECTORY / 'gate.sqlite3', DPAPIProtector())
        lock = original.RunLock(original.DIRECTORY / 'autofocus-draft.lock')
        def checkpoint():
            if (original.DIRECTORY / 'STOP').exists():
                raise ConsoleError('navigation_exit_emergency_stop')
        report = recover(store, lock, args.account, WindowsProcessObserver(),
                         apply=args.recover_exited_selection, checkpoint=checkpoint)
    except Exception as exc:
        report = dict(kind='exited_navigation_recovery', state='failed', send_invoked=False, api_called=False,
                      reason=str(exc) if isinstance(exc, ConsoleError) else 'navigation_exit_recovery_failed')
    finally:
        if store:
            store.close()
    path = original.DIRECTORY / ('exited-navigation-' + uuid.uuid4().hex + '.json')
    raw = (json.dumps(report, indent=2) + '\n').encode()
    with path.open('xb') as output:
        output.write(raw)
    print(json.dumps(dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(),
                          state=report['state'], reason=report.get('reason'))))
    return 1 if report['state'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
