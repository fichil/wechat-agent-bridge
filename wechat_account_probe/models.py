"""Non-sensitive errors and JSON-safe result aggregation."""
from dataclasses import dataclass, asdict


class ProbeError(Exception):
    """Only application-defined reason codes may cross the worker boundary."""


def failure_result(reason):
    result = aggregate([], [dict(reason=reason)])
    result.update(error=reason, exit_code=1, client_instances=[])
    return result


@dataclass(frozen=True)
class Instance:
    pid: int
    created: int
    session_id: int
    version: str


def instance_result(instance, **values):
    result = dict(wxid=None, wechat_id=None, nickname=None, sources={},
                  status='unavailable', reason='', instances=[asdict(instance)],
                  avatar=None, avatar_status='unavailable',
                  avatar_reason='account_identity_unconfirmed')
    result.update(values)
    return result


def aggregate(results, issues=()):
    accounts, skipped, by_id = [], [], {}
    for result in results:
        if result['status'] == 'not_logged_in':
            skipped.append(result)
            continue
        wxid = result.get('wxid')
        if wxid and wxid in by_id:
            previous = by_id[wxid]
            previous['instances'].extend(result['instances'])
            merge_avatar(previous, result)
            if any(previous.get(k) != result.get(k) for k in ('wechat_id', 'nickname')):
                previous.update(wechat_id=None, nickname=None, status='partial',
                                reason='cross_instance_fields_conflict')
                previous['sources'] = {k: v for k, v in previous['sources'].items()
                                       if k in ('wxid', 'avatar')}
            elif result['status'] != 'complete':
                previous.update(status='partial', reason=result['reason'])
        else:
            accounts.append(result)
            if wxid:
                by_id[wxid] = result
    issues = list(issues)
    complete = not issues and all(a['status'] == 'complete' for a in accounts)
    return dict(schema_version=2, scope='current_windows_user_all_sessions',
                accounts=accounts, skipped_instances=skipped, issues=issues,
                summary=dict(confirmed_accounts=sum(bool(a['wxid']) for a in accounts),
                             complete_accounts=sum(a['status'] == 'complete' for a in accounts),
                             avatar_available_accounts=sum(a['avatar_status'] == 'available'
                                                           for a in accounts),
                             unresolved_instances=sum(len(a['instances']) for a in accounts
                                                      if not a['wxid']),
                             coverage_complete=complete), exit_code=0 if complete else 2)


def merge_avatar(previous, current):
    """Cross-session conflicts affect the avatar only and cannot be overwritten."""
    if previous['avatar_reason'] == 'avatar_instances_conflict':
        return
    if previous['avatar_status'] == current['avatar_status'] == 'available':
        if previous['avatar'] != current['avatar']:
            previous.update(avatar=None, avatar_status='unavailable',
                            avatar_reason='avatar_instances_conflict')
            previous['sources'].pop('avatar', None)
    elif previous['avatar_status'] != 'available' and current['avatar_status'] == 'available':
        previous.update(avatar=current['avatar'], avatar_status='available', avatar_reason='')
        if 'avatar' in current['sources']:
            previous['sources']['avatar'] = current['sources']['avatar']
