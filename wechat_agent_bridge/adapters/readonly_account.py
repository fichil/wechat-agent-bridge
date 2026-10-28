"""Read-only own-account metadata. No UI actions, grants or provider access."""
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
import re
import sqlite3

from ..console_models import Account, ConsoleError
from .account_discovery import ID_LABEL, ID_VALUE


OWN_PROFILE_SOURCE = 'uia_own_profile_exact_id_user_selected_window'
RECORD_SOURCE = 'current_process_verified_own_identity_record'
PUBLIC_SOURCE = 'uia_own_navigation_labeled_fields'


def labeled_fields(values):
    """Only explicitly labeled fields from an independently proven own node."""
    text = '\n'.join(value for value in values if isinstance(value, str))
    ids = set(re.findall(r'^' + ID_LABEL + r'\s*[:：]?\s*(' + ID_VALUE + r')\s*$',
                         text, re.MULTILINE | re.IGNORECASE))
    names = {name.strip() for name in re.findall(r'^(?:昵称|Nickname)\s*[:：]\s*([^\n]{1,128})$',
                                                text, re.MULTILINE | re.IGNORECASE)}
    if len(ids) > 1 or len(names) > 1:
        raise ConsoleError('account_identity_sources_conflict')
    return dict(wechat_id=next(iter(ids), None), name=next(iter(names), None))


class OwnIdentityRecordReader:
    """SELECT one DPAPI record, using SQLite mode=ro and an explicit close."""
    def __init__(self, directory=None, protector=None):
        from ..acceptance import acceptance_directory
        self.path = (directory or acceptance_directory())/'gate.sqlite3'
        self.protector = protector

    def read(self, instance):
        from ..console_store import digest
        from ..protection import DPAPIProtector
        if not self.path.exists():
            return None
        try:
            with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
                row = db.execute('SELECT payload FROM records WHERE bucket=? AND key=?',
                                 ('console_identity', digest(instance))).fetchone()
            if row is None:
                return None
            if len(row[0]) > 65536:
                raise ValueError
            value = json.loads((self.protector or DPAPIProtector()).unprotect(row[0]))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except Exception:
            raise ConsoleError('account_identity_record_unreadable') from None


def verified_record_id(record, stamp):
    if record is None:
        return None
    pid, hwnd, created, _path, version = stamp
    fingerprint = record.get('fingerprint', {})
    if (record.get('instance_id') != f'{pid}:{hwnd}' or
            any(fingerprint.get(key) != expected for key, expected in
                (('pid', pid), ('hwnd', hwnd), ('created', created), ('version', version)))):
        raise ConsoleError('account_identity_record_stale')
    if (record.get('identity_certain') is not True or record.get('connected') is not True or
            record.get('identity_source') != OWN_PROFILE_SOURCE or
            not isinstance(record.get('wechat_id'), str) or
            not re.fullmatch(ID_VALUE, record['wechat_id'])):
        raise ConsoleError('account_identity_record_unverified')
    return record['wechat_id']


class ReadOnlyAccountDiscovery:
    """Recheck process and record every refresh; never invoke the profile backend."""
    def __init__(self, metadata, records=None):
        self.metadata = metadata
        self.records = records if records is not None else OwnIdentityRecordReader()

    def discover(self):
        result = []
        for window in self.metadata.windows():
            try:
                stamp = self.metadata.stamp(window)
            except ConsoleError:
                continue
            pid, hwnd, _created, _path, version = stamp
            instance = f'{pid}:{hwnd}'
            candidate = dict(id='candidate-'+hashlib.sha256(instance.encode()).hexdigest()[:32],
                             name='昵称未获取', instance_id=instance, version=version,
                             identity_certain=False, discovery_state='unavailable',
                             discovery_reason='account_own_fields_not_exposed', discovery_sources={})
            try:
                public = self.metadata.own_fields(window)
                record_id, record_reason = None, None
                try:
                    record_id = verified_record_id(self.records.read(instance), stamp)
                except ConsoleError as exc:
                    record_reason = str(exc)
                if self.metadata.stamp(window) != stamp:
                    raise ConsoleError('account_discovery_instance_changed')
                public_id = public.get('wechat_id')
                if public_id is not None and (not isinstance(public_id, str) or not re.fullmatch(ID_VALUE, public_id)):
                    raise ConsoleError('account_identity_sources_conflict')
                if public_id and record_id and public_id != record_id:
                    raise ConsoleError('account_identity_sources_conflict')
                wechat_id = public_id or record_id
                name = public.get('name')
                if name and (not isinstance(name, str) or not name.strip() or len(name) > 128):
                    raise ConsoleError('account_identity_sources_conflict')
                # Never turn an old ID-as-name bug into successful nickname extraction.
                if name == wechat_id:
                    name = None
                sources = candidate['discovery_sources']
                if name:
                    candidate['name'] = name
                    sources['name'] = PUBLIC_SOURCE
                if wechat_id:
                    candidate.update(wechat_id=wechat_id, identity_certain=True,
                                     id='wechat-'+hashlib.sha256(repr((wechat_id,)).encode()).hexdigest()[:32],
                                     identity_source=PUBLIC_SOURCE if public_id else RECORD_SOURCE)
                    sources['wechat_id'] = PUBLIC_SOURCE if public_id else RECORD_SOURCE
                if name and wechat_id:
                    candidate.update(discovery_state='identified', discovery_reason='')
                elif name or wechat_id:
                    candidate.update(discovery_state='partial', discovery_reason=(
                        'account_nickname_not_exposed' if wechat_id else
                        record_reason or 'account_wechat_id_not_exposed'))
                elif record_reason:
                    candidate['discovery_reason'] = record_reason
            except ConsoleError as exc:
                candidate['discovery_reason'] = str(exc)
            except Exception:
                candidate['discovery_reason'] = 'account_discovery_failed'
            result.append(asdict(Account(**candidate)))
        return result
