"""Explicit, protected read of at most 50 rows in the bound test group.

This research entry point never selects a chat, writes input, invokes Send or
uploads content. Public reports contain shapes only; labels remain DPAPI data.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import probe_background_draft as original
from scripts.uia_client_bootstrap import prepare_client_elements, read_pattern_context, require_safe_context
from wechat_agent_bridge.console_models import ConsoleError


from wechat_agent_bridge.adapters.bound_read import collect_labels


def protect_capture(value, directory, protector):
    data = json.dumps(value, ensure_ascii=False).encode('utf-8')
    encrypted = protector.protect(data)  # No plaintext fallback or temporary file.
    path = Path(directory) / ('bound-messages-' + uuid.uuid4().hex + '.dpapi')
    with path.open('xb') as stream:
        stream.write(encrypted)
    return dict(protected_capture=str(path), protected_sha256=hashlib.sha256(encrypted).hexdigest(),
                loaded_count=value['loaded_count'], retained_rows=len(value['rows']),
                coverage=value['coverage'], historical=True,
                label_shapes=[dict(length=len(row['label']), lines=len(row['label'].splitlines()),
                                   class_name=row['class_name']) for row in value['rows']],
                sender_identity_verified=False, mention_semantics_verified=False)


def worker(pipe, fp, names, expected, write):
    sys.coinit_flags = 0
    try:
        if write:
            raise ConsoleError('message_inspection_write_forbidden')
        client, root, element, pattern, legacy, uia = prepare_client_elements(fp, expected)
        from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
        from wechat_agent_bridge.protection import DPAPIProtector
        backend = WindowsUIA()
        current = backend.target_control_probe(fp['pid'], fp['hwnd'], names)
        original.check_draft_target(expected, current)
        original._exchange(pipe, 'verified', dict(target=current, root_runtime_id=list(root.GetRuntimeId())))
        original._exchange(pipe, 'checkpoint', 'before_bound_message_labels')
        require_safe_context(read_pattern_context(client._client, element, pattern))
        value = collect_labels(client._client, root, uia, fp['pid'])
        current = backend.target_control_probe(fp['pid'], fp['hwnd'], names)
        original.check_draft_target(expected, current)
        original._exchange(pipe, 'checkpoint', 'after_bound_message_labels')
        protected = protect_capture(value, original.DIRECTORY, DPAPIProtector())
        pipe.send(dict(kind='result', value=dict(state='preflight_verified', attempted=False,
                       setvalue_called=False, invoke_called=False, **protected)))
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ConsoleError) else 'message_inspection_failed'
        try:
            pipe.send(dict(kind='error', value=reason))
        except (OSError, EOFError):
            pass
    finally:
        pipe.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--inspect-bound', action='store_true')
    parser.add_argument('--allow-accessibility-patch', action='store_true')
    for flag in ('account', 'group', 'private', 'target-evidence', 'synthetic-evidence', 'virtual-desktop'):
        parser.add_argument('--' + flag)
    args = parser.parse_args(argv)
    if not args.inspect_bound:
        print(json.dumps(dict(state='disabled', send_enabled=False)))
        return 0
    args.run, args.preflight, args.operator_idle = False, True, False
    try:
        from scripts.probe_bound_uia_context import require_context_canary
        if not args.synthetic_evidence or not args.virtual_desktop:
            raise ConsoleError('context_evidence_and_desktop_required')
        require_context_canary(args.synthetic_evidence)
        report = original.run(args, taskview_desktop_id=args.virtual_desktop, inspection_worker=worker)
    except Exception as exc:
        report = dict(state='failed', reason=str(exc) if isinstance(exc, ConsoleError)
                      else 'message_inspection_preparation_failed', attempted=False)
    report.update(kind='bound_protected_message_labels', send_invoked=False, api_called=False,
                  production_enabled=False, collector_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    path = original.DIRECTORY / ('message-labels-' + uuid.uuid4().hex + '.json')
    with path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(dict(path=str(path), state=report['state'], reason=report.get('reason'),
                         patch_restored=report.get('patch_restored'))))
    return 0 if report['state'] == 'preflight_verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
