"""Opt-in account-only rebind after a verified client-process replacement.

Reads only the already-open own-profile card. No target/draft/history read,
navigation, send, provider call or production capability promotion exists.
Archive and new binding are committed only after worker exit and byte restore.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import re
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import probe_background_draft as original
from scripts.development_navigation import cleanup_resources, input_tick
from scripts.development_window_restore import load_local_hashed
from scripts.recover_exited_navigation import WindowsProcessObserver, require_gone, content_hash
from scripts.contact_profile_identity import unchanged_readonly, sha
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.console_store import digest

SOURCE = 'uia_own_profile_exact_id_user_selected_window'
PENDING = ('patch', 'thread_hook', 'draft_probe', 'development_navigation',
           'development_window_restore', 'development_exact_search', 'account_rebind')


def sources():
    names = ('scripts/rebind_sender_profile.py', 'scripts/own_navigation_metadata.py', 'scripts/own_avatar_metadata.py',
             'scripts/own_avatar_hit_test.py',
             'scripts/own_avatar_msaa.py',
             'scripts/window_oleacc.py',
             'scripts/window_oleacc_hit.py',
             'scripts/own_profile_structure.py',
             'scripts/inspect_contacts_navigation.py', 'scripts/development_window_restore.py',
             'scripts/recover_exited_navigation.py',
             'scripts/development_navigation.py', 'wechat_agent_bridge/adapters/accessibility_gate.py',
             'wechat_agent_bridge/adapters/windows_uia.py', 'wechat_agent_bridge/adapters/uia_client.py')
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


def require_receipts(fingerprint_report, fingerprint_sha, cua, account):
    from wechat_agent_bridge.adapters.accessibility_gate import Fingerprint, VERSION, DLL_SHA256, FLAG_RVA
    if not isinstance(account, str) or not re.fullmatch(r'[A-Za-z0-9_-]{6,64}', account):
        raise ConsoleError('sender_rebind_exact_account_required')
    try:
        fp = Fingerprint(**fingerprint_report['fingerprint'])
    except (KeyError, TypeError):
        raise ConsoleError('sender_rebind_fingerprint_evidence_required') from None
    if (fingerprint_report.get('kind') != 'replacement_window_fingerprint_readonly' or
            fp.version != VERSION or fp.dll_sha256 != DLL_SHA256 or fp.flag_rva != FLAG_RVA or
            any(type(v) is not int or v <= 0 for v in (fp.pid, fp.hwnd, fp.created, fp.module_base, fp.module_size)) or
            fp.module_size <= fp.flag_rva or type(fingerprint_report.get('accessibility_byte')) is not int or
            fingerprint_report['accessibility_byte'] != 0 or
            fingerprint_report.get('patch_enabled') is not False or
            fingerprint_report.get('target_controls_read') is not False or
            fingerprint_report.get('history_read') is not False or
            fingerprint_report.get('send_invoked') is not False or fingerprint_report.get('api_called') is not False):
        raise ConsoleError('sender_rebind_fingerprint_evidence_required')
    if (not isinstance(cua, dict) or cua.get('kind') != 'development_cua_own_identity' or
            cua.get('state') != 'own_profile_visually_verified' or
            cua.get('source') != 'computer_use_own_avatar_card' or cua.get('role') != 'sender' or
            cua.get('account_sha256') != sha(account) or cua.get('window_id') != fp.hwnd or
            cua.get('fingerprint_evidence_sha256') != fingerprint_sha or
            any(cua.get(key) is not False for key in
                ('text_input', 'draft_read', 'send_invoked', 'api_called', 'native_uia_or_patch_used'))):
        raise ConsoleError('sender_rebind_current_own_card_required')
    return fp


def require_account(account, expected, fp):
    if (not isinstance(account, dict) or account.get('wechat_id') != expected or
            account.get('instance_id') != f'{fp.pid}:{fp.hwnd}' or account.get('identity_source') != SOURCE or
            not isinstance(account.get('id'), str) or not account['id']):
        raise ConsoleError('sender_rebind_exact_account_reply_required')


def require_input_scope(before_tick, current_tick, before, current, *, metadata_only=False):
    """Input in another app cannot edit a text-free metadata-only result.

    Focus, clipboard, lock, geometry, source and process guards still apply.
    Binding always requires the original strict global-input condition.
    """
    if current_tick != before_tick and (not metadata_only or before.target_active or current.target_active):
        raise ConsoleError('sender_rebind_human_takeover')


def require_metadata_environment(before, current, events, observer_failed=False):
    """Permit other-app activity only for diagnostics that cannot bind or act.

    Non-target events stay in the report and are not background qualification.
    Missing attribution is never accepted as a target-safe event.
    """
    if observer_failed or before.locked or current.locked:
        raise ConsoleError('sender_metadata_desktop_unavailable')
    if before.clipboard_sequence != current.clipboard_sequence:
        raise ConsoleError('sender_metadata_clipboard_changed')
    if any(event.get('target_process') is not False for event in events):
        raise ConsoleError('sender_metadata_target_or_unattributed_event')
    if before.target_active or current.target_active:
        unchanged_readonly(before, current)


def commit_binding(store, old, account, fp, report, pending, check):
    """One SQLite transaction; never rewrites contacts, baselines or quotas."""
    require_account(account, old['account']['wechat_id'], fp)
    if (report.get('state') != 'profile_observed' or report.get('patch_restored') is not True or
            report.get('worker_exited') is not True or report.get('cleanup_errors') != [] or
            report.get('events') != [] or report.get('sources_unchanged') is not True):
        raise ConsoleError('sender_rebind_clean_exit_required')
    old_key = content_hash(old['fingerprint'])
    new = dict(account=account, fingerprint=asdict(fp), previous_fingerprint_sha256=old_key,
               requires_fresh_target_identity=True)
    finished = {**report, 'state': 'sender_rebound', 'account_binding_modified': True}
    with store.lock:
        store.db.execute('BEGIN IMMEDIATE')
        try:
            check()
            if (store.get('binding', 'account') != old or store.get('account_rebind', 'pending') != pending or
                    any(store.get(name, 'pending') for name in PENDING if name != 'account_rebind')):
                raise ConsoleError('sender_rebind_original_state_changed')
            archive = store.get('binding_epochs', old_key)
            if archive is not None and archive != old:
                raise ConsoleError('sender_rebind_prior_epoch_conflict')
            records = [('binding', 'account', new), ('account_rebind', report['request_id'], finished),
                       ('account_rebind', 'pending', None)]
            if archive is None:
                records.insert(0, ('binding_epochs', old_key, old))
            for bucket, key, value in records:
                store.db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',
                                 (bucket, digest(key), store._pack(value)))
            store.db.commit()
        except BaseException:
            store.db.rollback()
            raise
    return finished


def worker(pipe, fp, account):
    sys.coinit_flags = 0
    try:
        from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient
        client = BackgroundUIAClient()
        from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
        backend = WindowsUIA()
        first = backend.verify_own_profile(fp['pid'], fp['hwnd'], account)
        original._exchange(pipe, 'checkpoint', 'own_profile_identity_recheck')
        second = backend.verify_own_profile(fp['pid'], fp['hwnd'], account)
        client.require_background_mode()
        if first != second:
            raise ConsoleError('sender_rebind_profile_changed')
        pipe.send(dict(kind='result', account=first))
    except Exception as exc:
        pipe.send(dict(kind='error', reason=str(exc) if isinstance(exc, ConsoleError) else 'sender_rebind_worker_failed'))
    finally:
        pipe.close()


def run(args):
    if not (args.account and args.allow_accessibility_patch and args.fingerprint_evidence and args.cua_evidence):
        raise ConsoleError('sender_rebind_explicit_scope_required')
    fingerprint_report, fingerprint_sha = load_local_hashed(args.fingerprint_evidence)
    cua, cua_sha = load_local_hashed(args.cua_evidence)
    fp = require_receipts(fingerprint_report, fingerprint_sha, cua, args.account)
    hit_test_only = getattr(args, 'inspect_own_avatar_hit_test', False)
    native_msaa_only = getattr(args, 'inspect_own_avatar_native_msaa', False)
    window_oleacc_only = getattr(args, 'inspect_window_oleacc', False)
    window_hit_only = getattr(args, 'inspect_window_oleacc_hit', False)
    profile_structure_only = getattr(args,'inspect_own_profile_structure',False)
    metadata_only = getattr(args, 'inspect_own_navigation', False) or hit_test_only or native_msaa_only or window_oleacc_only or window_hit_only or profile_structure_only
    from wechat_agent_bridge.adapters.accessibility_gate import NativeGate, AccessibilityGate
    from wechat_agent_bridge.adapters.background import snapshot
    from wechat_agent_bridge.adapters.desktop_events import DesktopEventObserver
    from wechat_agent_bridge.console_store import ConsoleStore
    from wechat_agent_bridge.protection import DPAPIProtector
    store = ConsoleStore(original.DIRECTORY / 'gate.sqlite3', DPAPIProtector())
    lock = original.RunLock(original.DIRECTORY / 'autofocus-draft.lock')
    native = gate = observer = process = parent = child = None
    report = dict(kind='sender_profile_rebind', state='failed', request_id='sender-rebind-' + uuid.uuid4().hex,
                  fingerprint=asdict(fp), sources=sources(), cleanup_errors=[], events=[],
                  account_binding_modified=False, history_read=False, draft_read=False,
                  target_controls_read=False, send_invoked=False, api_called=False, background_qualified=False,
                  fingerprint_evidence_sha256=fingerprint_sha, cua_evidence_sha256=cua_sha)
    if metadata_only:
        report['kind'] = 'own_navigation_metadata_inspection'
        report['input_policy'] = 'background_metadata_only_other_app_activity_recorded'
    pending = old = account = None
    started = time.monotonic()
    try:
        lock.acquire()
        if any(store.get(name, 'pending') for name in PENDING):
            raise ConsoleError('sender_rebind_prior_recovery_required')
        old = store.get('binding', 'account', {})
        if (old.get('account', {}).get('wechat_id') != args.account or
                old.get('account', {}).get('identity_source') != SOURCE or not old.get('fingerprint') or
                old['fingerprint'] == asdict(fp)):
            raise ConsoleError('sender_rebind_prior_exact_account_required')
        # Replacement may never inherit the previous process's authority.
        exit_observation = WindowsProcessObserver().inspect(old['fingerprint']['pid'])
        report['old_instance_exit_reason'] = require_gone(old['fingerprint'], exit_observation)
        native = NativeGate(fp.hwnd)
        if native.fingerprint() != fp:
            raise ConsoleError('sender_rebind_current_process_changed')
        baseline, tick = snapshot(fp.pid), input_tick()
        if baseline.locked:
            raise ConsoleError('desktop_locked_or_unavailable')
        pending = dict(request_id=report['request_id'], fingerprint=asdict(fp),
                       previous_binding_sha256=content_hash(old), lock_token=lock.token)
        store.put('account_rebind', 'pending', pending)
        observer = DesktopEventObserver(fp.pid).__enter__()

        def check():
            if (original.DIRECTORY / 'STOP').exists():
                raise ConsoleError('sender_rebind_emergency_stop')
            if time.monotonic() - started > 20:
                raise ConsoleError('sender_rebind_deadline')
            if (native.fingerprint() != fp or store.get('binding', 'account') != old or
                    store.get('account_rebind', 'pending') != pending or
                    lock.path.read_text(encoding='ascii') != lock.token):
                raise ConsoleError('sender_rebind_original_state_changed')
            if any(store.get(name, 'pending') for name in PENDING if name not in ('patch', 'account_rebind')):
                raise ConsoleError('sender_rebind_prior_recovery_required')
            if sources() != report['sources']:
                raise ConsoleError('sender_rebind_sources_changed')
            if (load_local_hashed(args.fingerprint_evidence)[1] != fingerprint_sha or
                    load_local_hashed(args.cua_evidence)[1] != cua_sha):
                raise ConsoleError('sender_rebind_evidence_changed')
            current = snapshot(fp.pid)
            if metadata_only:
                with observer.evidence.lock:
                    require_metadata_environment(baseline, current, observer.evidence.events, observer.evidence.failed)
            else:
                observer.check()
                unchanged_readonly(baseline, current)
            require_input_scope(tick, input_tick(), baseline, current, metadata_only=metadata_only)
            return True

        check()
        gate = AccessibilityGate(native, allowed=True, save=lambda value: store.put('patch', 'pending', value))
        report['patch'] = gate.enable(fp, check)
        context = mp.get_context('spawn')
        parent, child = context.Pipe()
        if profile_structure_only:
            from scripts.own_profile_structure import worker as chosen_worker
        elif window_hit_only:
            from scripts.window_oleacc_hit import worker as chosen_worker
        elif window_oleacc_only:
            from scripts.window_oleacc import worker as chosen_worker
        elif native_msaa_only:
            from scripts.own_avatar_msaa import worker as chosen_worker
        elif hit_test_only:
            from scripts.own_avatar_hit_test import worker as chosen_worker
        elif metadata_only:
            from scripts.own_navigation_metadata import worker as chosen_worker
        else:
            chosen_worker = worker
        process = context.Process(target=chosen_worker, args=(child, asdict(fp), args.account))
        process.start()
        child.close()
        while True:
            check()
            if not parent.poll(.03):
                if not process.is_alive():
                    raise ConsoleError('sender_rebind_worker_exited_without_result')
                continue
            item = parent.recv()
            if item.get('kind') == 'checkpoint':
                parent.send({'continue': check()})
            elif metadata_only and item.get('kind') == 'navigation_metadata':
                metadata = item.get('metadata', {})
                if (metadata.get('names_or_text_or_values_read') is not False or
                        metadata.get('action_executed') is not False or
                        metadata.get('own_avatar_identity_verified') is not False):
                    raise ConsoleError('sender_rebind_navigation_metadata_invalid')
                report.update(state='own_navigation_metadata_observed', navigation_metadata=metadata)
                check()
                break
            elif item.get('kind') == 'result':
                if metadata_only:
                    raise ConsoleError('sender_rebind_navigation_cannot_bind_account')
                account = item.get('account')
                require_account(account, args.account, fp)
                check()
                report['state'] = 'profile_observed'
                break
            elif item.get('kind') == 'error':
                raise ConsoleError(item['reason'])
            else:
                raise ConsoleError('sender_rebind_protocol_invalid')
    except Exception as exc:
        report.update(state='failed', reason=str(exc) if isinstance(exc, ConsoleError) else 'sender_rebind_preparation_failed')
    finally:
        if parent:
            try:
                parent.send({'continue': False})
            except (OSError, EOFError):
                pass
        cleanup_resources(report, process=process, gate=gate, observer=observer,
                          resources=(('parent', parent), ('child', child)))
        if native:
            try:
                if native.fingerprint() != fp:
                    raise ConsoleError('sender_rebind_process_changed_during_cleanup')
            except Exception:
                report['cleanup_errors'].append('sender_rebind_native_cleanup_failed')
            finally:
                try:
                    native.close()
                except Exception:
                    report['cleanup_errors'].append('sender_rebind_native_close_failed')
        report['sources_unchanged'] = sources() == report['sources']
        if report['state'] == 'own_navigation_metadata_observed':
            report['non_target_events_recorded'] = sum(event.get('target_process') is False for event in report['events'])
            if (not report['sources_unchanged'] or report['cleanup_errors'] or
                    any(event.get('target_process') is not False for event in report['events'])):
                report.update(state='failed', reason='sender_rebind_navigation_cleanup_unconfirmed')
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        try:
            if report['state'] == 'profile_observed':
                def commit_check():
                    if (sources() != report['sources'] or input_tick() != tick or
                            lock.path.read_text(encoding='ascii') != lock.token):
                        raise ConsoleError('sender_rebind_changed_before_commit')
                    unchanged_readonly(baseline, snapshot(fp.pid))
                try:
                    report = commit_binding(store, old, account, fp, report, pending, commit_check)
                except Exception as exc:
                    # Commit uncertainty preserves the lock and recovery record;
                    # the next process cannot assume the old or new epoch won.
                    report.update(state='recovery_required', binding_commit_unknown=True,
                                  reason=str(exc) if isinstance(exc, ConsoleError) else 'sender_rebind_commit_failed')
            if report['state'] != 'sender_rebound':
                store.put('account_rebind', report['request_id'], report)
                if (pending is not None and report['state'] != 'recovery_required' and
                        not report['cleanup_errors'] and not store.get('patch', 'pending') and
                        store.get('account_rebind', 'pending') == pending):
                    store.put('account_rebind', 'pending', None)
            if (report['state'] != 'recovery_required' and not store.get('patch', 'pending') and
                    not store.get('account_rebind', 'pending')):
                lock.release()
        finally:
            store.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--bind-current-profile', action='store_true')
    mode.add_argument('--inspect-own-navigation', action='store_true')
    mode.add_argument('--inspect-own-avatar-hit-test', action='store_true')
    mode.add_argument('--inspect-own-avatar-native-msaa', action='store_true')
    mode.add_argument('--inspect-window-oleacc', action='store_true')
    mode.add_argument('--inspect-window-oleacc-hit', action='store_true')
    mode.add_argument('--inspect-own-profile-structure',action='store_true')
    parser.add_argument('--allow-accessibility-patch', action='store_true')
    for name in ('account', 'fingerprint-evidence', 'cua-evidence'):
        parser.add_argument('--' + name)
    args = parser.parse_args(argv)
    if not (args.bind_current_profile or args.inspect_own_navigation or args.inspect_own_avatar_hit_test or
            args.inspect_own_avatar_native_msaa or args.inspect_window_oleacc or args.inspect_window_oleacc_hit or args.inspect_own_profile_structure):
        print(json.dumps(dict(state='disabled', account_binding_modified=False)))
        return 0
    try:
        report = run(args)
    except Exception as exc:
        report = dict(kind='sender_profile_rebind', state='failed', account_binding_modified=False,
                      reason=str(exc) if isinstance(exc, ConsoleError) else 'sender_rebind_coordinator_failed')
    path = original.DIRECTORY / ('sender-profile-rebind-' + uuid.uuid4().hex + '.json')
    raw = (json.dumps(report, indent=2) + '\n').encode()
    with path.open('xb') as output:
        output.write(raw)
    print(json.dumps(dict(path=str(path), state=report['state'], reason=report.get('reason'),
                          sha256=hashlib.sha256(raw).hexdigest())))
    return 0 if report['state'] in ('sender_rebound', 'own_navigation_metadata_observed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
