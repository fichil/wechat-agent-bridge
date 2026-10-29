"""Bounded sequential workers. Workers return fields, never memory/key material."""
from dataclasses import asdict
import json
import os
import subprocess
import sys
import time

from .models import ProbeError, aggregate, instance_result, failure_result


def probe_instance(instance, deadline):
    from .native import ProcessMemory
    from .memory import current_account, bind_directory, mapped_account_directories, contact_key
    from .sqlcipher import stable_snapshot, decrypt_snapshot, read_own_row
    from .avatar import load_avatar, AVATAR_SOURCE
    result = instance_result(instance)
    if not instance.version.startswith('4.1.'):
        result['reason'] = 'client_version_unsupported'
        return result
    memory = ProcessMemory(instance, deadline)
    try:
        paths = memory.mapped_paths()
        wxid = current_account(memory, paths)
        if not wxid:
            if mapped_account_directories(paths):
                raise ProbeError('login_state_uncertain')
            result['status'] = 'not_logged_in'
            return result
        directory = bind_directory(paths, wxid)
        # Publish no ID until process configuration AND mapped directory agree.
        result['wxid'] = wxid
        result['sources']['wxid'] = 'current_process_configuration_and_mapped_directory'
        result['status'] = 'partial'
        contact = directory / 'db_storage' / 'contact' / 'contact.db'
        database, wal = stable_snapshot(contact, memory.check)
        key, salt = contact_key(memory, database[:4096])
        image = decrypt_snapshot(database, wal, key, salt, check=memory.check)
        fields = read_own_row(image, wxid)
        result.update(fields)
        for field in ('wechat_id', 'nickname'):
            if result[field] is not None:
                result['sources'][field] = 'hmac_verified_current_account_contact_row'
        # Release the decrypted contact image before opening the avatar database.
        del image, database, wal, key, salt
        result.update(load_avatar(memory, directory, wxid, deadline))
        if result['avatar_status'] == 'available':
            result['sources']['avatar'] = AVATAR_SOURCE
        # Rebind after the potentially slow scan; never return a switched account.
        memory.check()
        after_paths = memory.mapped_paths()
        if (current_account(memory, after_paths) != wxid or
                bind_directory(after_paths, wxid) != directory):
            raise ProbeError('account_changed_during_read')
        result['status'] = 'complete' if result['wxid'] and result['nickname'] else 'partial'
        result['reason'] = '' if result['status'] == 'complete' else 'self_contact_fields_missing'
    except ProbeError as exc:
        result['reason'] = str(exc)
        still_bound = False
        if result['wxid'] and str(exc) not in ('account_changed_during_read',
                                               'process_instance_changed', 'process_exited'):
            try:
                memory.check()
                current_paths = memory.mapped_paths()
                still_bound = (current_account(memory, current_paths) == result['wxid'] and
                               bind_directory(current_paths, result['wxid']) == directory)
            except (ProbeError, OSError):
                pass
        if not still_bound:
            result.update(wxid=None, wechat_id=None, nickname=None, sources={}, status='unavailable',
                          avatar=None, avatar_status='unavailable',
                          avatar_reason='account_identity_unconfirmed')
    except FileNotFoundError:
        result.update(wxid=None, wechat_id=None, nickname=None, sources={}, status='unavailable',
                      avatar=None, avatar_status='unavailable',
                      avatar_reason='account_identity_unconfirmed')
        result['reason'] = 'self_contact_database_missing'
    finally:
        memory.close()
    return result


def worker_response(instance, timeout):
    try:
        import psutil
        psutil.Process().nice(psutil.IDLE_PRIORITY_CLASS)
        result = probe_instance(instance, time.monotonic() + timeout)
    except ProbeError as exc:
        result = instance_result(instance)
        result['reason'] = str(exc)
    except Exception:
        result = instance_result(instance)
        result['reason'] = 'worker_failed'  # exception text may contain account/key data
    return result


def run_workers(instances, *, per_instance_timeout=60, total_timeout=180, launcher=None):
    launch = launcher or subprocess.Popen
    deadline, results = time.monotonic() + total_timeout, []
    for instance in instances:
        remaining = min(per_instance_timeout, deadline-time.monotonic())
        if remaining <= 0:
            result = instance_result(instance)
            result['reason'] = 'total_timeout'
            results.append(result)
            continue
        worker = None
        try:
            # Explicitly suppress console allocation/activation. Do not change the
            # host application's multiprocessing executable or global context.
            worker = launch([sys.executable, '-B', '-X', 'utf8', '-m',
                             'wechat_account_probe.worker'], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=os.path.dirname(os.path.dirname(__file__)),
                            creationflags=0x08000000 | 0x40)
            payload = json.dumps(dict(instance=asdict(instance), timeout=remaining)).encode()
            try:
                output, _stderr = worker.communicate(payload, timeout=remaining)
                result = json.loads(output)
                if (worker.returncode != 0 or result.get('instances') != [asdict(instance)] or
                        result.get('status') not in ('complete','partial','unavailable','not_logged_in')):
                    raise ValueError
            except subprocess.TimeoutExpired:
                result = instance_result(instance)
                result['reason'] = 'instance_timeout'
            except (ValueError, TypeError):
                result = instance_result(instance)
                result['reason'] = 'worker_disconnected'
            results.append(result)
        finally:
            if worker is not None:
                if worker.poll() is None:
                    worker.kill()  # solely the Popen object we created, never a WeChat PID
                worker.communicate(timeout=2)
    return results


def discover_accounts():
    """Explicit real scan; always return a JSON-serializable response dictionary."""
    try:
        return _discover_accounts()
    except ProbeError as exc:
        return failure_result(str(exc))
    except ImportError:
        return failure_result('probe_dependency_missing')
    except Exception:
        return failure_result('probe_failed')


def _discover_accounts():
    from .native import enumerate_instances
    from .observation import Observation
    if os.name != 'nt':
        raise ProbeError('windows_x64_required')
    instances, issues = enumerate_instances()
    started = time.monotonic()
    with Observation(p.pid for p in instances) as observation:
        results = run_workers(instances)
        after, after_issues = enumerate_instances()
    issues.extend(after_issues)
    if set(instances) != set(after):
        issues.append(dict(reason='process_set_changed_during_scan'))
    result = aggregate(results, issues)
    result['observation'] = observation.result
    result['duration_seconds'] = round(time.monotonic()-started, 3)
    result['client_instances'] = [asdict(instance) for instance in instances]
    return result
