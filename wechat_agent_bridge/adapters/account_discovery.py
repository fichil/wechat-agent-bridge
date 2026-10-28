"""Own-account metadata discovery, independent of grants, chat and providers.

The backend must establish that a profile belongs to the account owner before
returning its fields. Merely finding a ProfileUniquePop is not that proof.
"""
from dataclasses import asdict
from contextlib import closing
import hashlib
import re
import json
import sqlite3
import time

from ..console_models import Account, ConsoleError


ID_LABEL = r'(?:微信号|WeChat\s*ID)'
ID_VALUE = r'[A-Za-z0-9_-]{6,64}'


def window_in_live_use(hwnd, *, directory=None, protector=None):
    """Read only the existing byte-ownership record; never disturb a live service."""
    from ..acceptance import acceptance_directory
    from ..console_store import digest
    from ..protection import DPAPIProtector
    path = (directory or acceptance_directory())/'gate.sqlite3'
    if not path.exists():
        return False
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=1)) as connection:
            row = connection.execute('SELECT payload FROM records WHERE bucket=? AND key=?',
                                     ('patch', digest('pending'))).fetchone()
        if not row:
            return False
        record = json.loads((protector or DPAPIProtector()).unprotect(row[0]))
        return bool(record and record.get('fingerprint', {}).get('hwnd') == hwnd)
    except Exception:
        return True  # Uncertain ownership must not trigger a foreground action.


def profile_fields(values):
    """Parse only fields from a proven own card; never infer an ID from a name."""
    lines = list(dict.fromkeys(line.strip() for value in values
                              for line in value.splitlines() if line.strip()))
    joined = '\n'.join(lines)
    ids = set(re.findall(r'^' + ID_LABEL + r'\s*[:：]?\s*(' + ID_VALUE + r')\s*$',
                         joined, re.IGNORECASE | re.MULTILINE))
    if len(ids) != 1:
        raise ConsoleError('account_profile_id_missing_or_ambiguous')
    wechat_id = ids.pop()
    names = set(re.findall(r'^(?:昵称|Nickname)\s*[:：]\s*([^\n]{1,128})$', joined,
                           re.IGNORECASE | re.MULTILINE))
    if not names:
        # This card layout puts the display name immediately before the ID row.
        # Ignore duplicated ancestor text and known labels, never use the ID.
        position = next((i for i, line in enumerate(lines)
                         if re.match(ID_LABEL, line, re.IGNORECASE)), 0)
        candidates = [line for line in lines[:position] if line not in
                      ('个人信息', '我的资料', 'My Profile', '微信', 'WeChat') and
                      line != wechat_id and not re.search(r'[:：]', line) and len(line) <= 128]
        if len(candidates) == 1:
            names = set(candidates)
    if len(names) != 1:
        raise ConsoleError('account_profile_nickname_missing_or_ambiguous')
    return dict(name=names.pop(), wechat_id=wechat_id)


class OwnAccountDiscovery:
    """Process-lifetime cache; no persisted account identities or permissions."""
    def __init__(self, backend, clock=time.monotonic):
        self.backend, self.clock = backend, clock
        self.cache = {}

    def discover(self):
        result, live = [], set()
        for window in self.backend.windows():
            try:
                stamp = self.backend.stamp(window)
            except ConsoleError:
                continue  # An exited window is not a connected account.
            key = tuple(stamp)
            live.add(key)
            cached = self.cache.get(key)
            if cached and self.clock() - cached[0] < 15:
                result.append(cached[1])
                continue
            pid, hwnd, _created, _path, version = stamp
            instance = f'{pid}:{hwnd}'
            candidate = dict(id='candidate-' + hashlib.sha256(instance.encode()).hexdigest()[:32],
                             name='昵称未获取', instance_id=instance, version=version,
                             identity_certain=False, discovery_state='unavailable')
            try:
                values = self.backend.own_profile(window)
                if self.backend.stamp(window) != stamp:
                    raise ConsoleError('account_discovery_instance_changed')
                fields = profile_fields(values)
                candidate.update(fields, id='wechat-' + hashlib.sha256(
                    repr((fields['wechat_id'],)).encode()).hexdigest()[:32],
                    identity_certain=True, identity_source='uia_own_profile_automatic',
                    discovery_state='identified', discovery_reason='')
            except ConsoleError as exc:
                # Reasons are application constants, never UI strings.
                candidate['discovery_reason'] = str(exc)
            except Exception:
                candidate['discovery_reason'] = 'account_discovery_failed'
            account = asdict(Account(**candidate))
            self.cache[key] = (self.clock(), account)
            result.append(account)
        self.cache = {key: value for key, value in self.cache.items() if key in live}
        return result
