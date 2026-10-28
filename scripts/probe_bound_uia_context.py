"""Explicit read-only UIA context check of the already bound test group.

The controller reuses exact account/window/target/desktop checks and restores
the approved accessibility byte. The worker reads only its own UIA objects;
there is no SetValue, Invoke, send, internal method call or budget claim.
"""
import argparse
import ctypes as c
import json
from pathlib import Path
import platform
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import probe_background_draft as original
from scripts import probe_uia_node_context as canary
from scripts.uia_context_evidence import ContextError, inspect_context
from scripts.uia_context_native import SelfMemory
from scripts.uia_client_bootstrap import prepare_client_elements
from scripts.uia_client_bootstrap import require_safe_context
from wechat_agent_bridge.console_models import ConsoleError
from scripts.message_control_schema import collect as collect_message_schema


def require_context_canary(path):
    path = Path(path).resolve()
    if path.parent != original.DIRECTORY.resolve() or path.stat().st_size > 1_000_000:
        raise ConsoleError('local_context_evidence_required')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    with (canary.tree.replace_attributes(canary.tree, PHASES=canary.PHASES,
                                         source_qualified=canary.source_qualified)):
        complete = canary.cross.complete_pass(value)
    if (not complete or value.get('passed') is not True or
            value.get('kind') != 'self_process_readonly_uia_context' or
            value.get('fingerprints') != canary.fingerprints() or
            value.get('os') != platform.version()):
        raise ConsoleError('context_canary_missing_or_stale')


def prepare_send_element(prepared, fp):
    """Pin only structural send controls before constructing the legacy reader."""
    client, root, _element, _pattern, _legacy, uia = prepared
    raw = client._client
    condition = raw.CreatePropertyCondition(uia.UIA_ClassNamePropertyId, 'mmui::ChatInputView')
    containers = root.FindAll(uia.TreeScope_Descendants, condition)
    if type(containers.Length) is not int or containers.Length != 1:
        raise ConsoleError('inspection_send_container_ambiguous')
    container = containers.GetElement(0)
    if container.CurrentProcessId != fp['pid']:
        raise ConsoleError('inspection_send_container_identity_mismatch')
    buttons = container.FindAll(uia.TreeScope_Descendants,
        raw.CreatePropertyCondition(uia.UIA_ControlTypePropertyId, uia.UIA_ButtonControlTypeId))
    if type(buttons.Length) is not int or not 0 <= buttons.Length <= 64:
        raise ConsoleError('inspection_send_control_limit')
    matches = []
    for index in range(buttons.Length):
        button = buttons.GetElement(index)
        if re.fullmatch(r'发送(?:\s*\([Ss]\))?|Send', button.CurrentName or ''):
            if button.CurrentProcessId != fp['pid'] or button.CurrentControlType != uia.UIA_ButtonControlTypeId:
                raise ConsoleError('inspection_send_identity_mismatch')
            matches.append(button)
    if len(matches) != 1:
        raise ConsoleError('inspection_send_button_ambiguous')
    button = matches[0]
    runtime = tuple(button.GetRuntimeId())
    pattern = button.GetCurrentPattern(uia.UIA_InvokePatternId).QueryInterface(uia.IUIAutomationInvokePattern)
    return container, button, pattern, runtime


def check_send_element(bundle, fp, uia):
    container, button, _pattern, runtime = bundle
    if (container.CurrentProcessId != fp['pid'] or button.CurrentProcessId != fp['pid'] or
            button.CurrentControlType != uia.UIA_ButtonControlTypeId or
            tuple(button.GetRuntimeId()) != runtime or
            not re.fullmatch(r'发送(?:\s*\([Ss]\))?|Send', button.CurrentName or '')):
        raise ConsoleError('inspection_send_identity_changed')


def inspection_worker(pipe, fp, names, expected, write, *, client_first=False, inspect_send=False,
                      inspect_messages=False, inspect_auxiliary=False):
    sys.coinit_flags = 0
    try:
        if write:
            raise ConsoleError('inspection_write_forbidden')
        if (inspect_send or inspect_messages or inspect_auxiliary) and not client_first:
            raise ConsoleError('inspection_send_requires_client_first')
        prepared = prepare_client_elements(fp, expected) if client_first else None
        send_bundle = prepare_send_element(prepared, fp) if inspect_send else None
        from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
        from wechat_agent_bridge.adapters.uia_draft_candidate import DraftTarget, _identity, _empty
        backend = WindowsUIA()
        current = backend.target_control_probe(fp['pid'], fp['hwnd'], names)
        root_runtime = tuple(backend.instances[f"{fp['pid']}:{fp['hwnd']}"][0].element_info.runtime_id)
        # The parent records only whitelisted scalar metadata, then enforces the
        # original exact target/draft check before allowing any context reads.
        original._exchange(pipe, 'verified', {'target': current, 'root_runtime_id': list(root_runtime)})
        original._exchange(pipe, 'checkpoint', 'before_readonly_context')
        client, root, element, pattern, legacy_loaded, uia = prepared or prepare_client_elements(fp, expected)
        raw = client._client
        target = DraftTarget(fp['hwnd'], fp['pid'], root_runtime, tuple(current['input_runtime_id']))
        _identity(root, element, target, uia)
        _empty(pattern)
        client.require_background_mode()
        memory = SelfMemory()
        result = {'state': 'preflight_verified', 'attempted': False, 'context_status': 'verified_observation'}
        result.update(client_initialized_before_legacy=client_first,
                      pywinauto_loaded_before_first_client=legacy_loaded)
        try:
            try:
                result['context'] = inspect_context(memory, memory.image,
                    c.cast(raw, c.c_void_p).value, c.cast(element, c.c_void_p).value,
                    c.cast(pattern, c.c_void_p).value, False)
            except ContextError as exc:
                result.update(state='context_unverified', context_status='unverified',
                              context_reason=str(exc), context=exc.evidence)
            memory.validate_source()
        finally:
            memory.close()
        _identity(root, element, target, uia)
        _empty(pattern)
        client.require_background_mode()
        if send_bundle is not None:
            require_safe_context(result['context'])
            check_send_element(send_bundle, fp, uia)
            memory = SelfMemory()
            try:
                send_context = inspect_context(memory, memory.image,
                    c.cast(raw, c.c_void_p).value, c.cast(send_bundle[1], c.c_void_p).value,
                    c.cast(send_bundle[2], c.c_void_p).value, False, pattern_kind='invoke')
                memory.validate_source()
            finally:
                memory.close()
            require_safe_context(send_context)
            check_send_element(send_bundle, fp, uia)
            result.update(send_context=send_context, send_context_guard_passed=True,
                          send_pattern_available=True, send_control_enabled=bool(send_bundle[1].CurrentIsEnabled),
                          send_runtime_id=list(send_bundle[3]), send_execution_verified=False)
        if inspect_messages:
            result['message_schema'] = collect_message_schema(raw, root, uia, fp['pid'],
                                                              auxiliary=inspect_auxiliary)
        current = backend.target_control_probe(fp['pid'], fp['hwnd'], names)
        original.check_draft_target(expected, current)
        original._exchange(pipe, 'checkpoint', 'after_readonly_context')
        result.update(identity_stable=True, empty_before_and_after=True, setvalue_called=False,
                      invoke_called=False, external_process_memory_read=False)
        pipe.send({'kind': 'result', 'value': result})
    except Exception as exc:
        reason = str(exc) if isinstance(exc, (ConsoleError, ContextError)) else 'readonly_context_worker_failed'
        try:
            pipe.send({'kind': 'error', 'value': reason})
        except (OSError, EOFError):
            pass
    finally:
        pipe.close()


def client_first_worker(pipe, fp, names, expected, write):
    return inspection_worker(pipe, fp, names, expected, write, client_first=True)


def send_context_worker(pipe, fp, names, expected, write):
    return inspection_worker(pipe, fp, names, expected, write, client_first=True, inspect_send=True)


def message_schema_worker(pipe, fp, names, expected, write):
    return inspection_worker(pipe, fp, names, expected, write, client_first=True,
                             inspect_send=True, inspect_messages=True)


def message_auxiliary_worker(pipe, fp, names, expected, write):
    return inspection_worker(pipe, fp, names, expected, write, client_first=True,
                             inspect_send=True, inspect_messages=True, inspect_auxiliary=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--inspect-bound', action='store_true')
    parser.add_argument('--client-first', action='store_true')
    parser.add_argument('--inspect-send-context', action='store_true')
    parser.add_argument('--inspect-message-schema', action='store_true')
    parser.add_argument('--inspect-message-auxiliary', action='store_true')
    for flag in ('account', 'group', 'private', 'target-evidence', 'synthetic-evidence', 'virtual-desktop'):
        parser.add_argument('--'+flag)
    parser.add_argument('--allow-accessibility-patch', action='store_true')
    args = parser.parse_args(argv)
    if not args.inspect_bound:
        print(json.dumps({'state': 'disabled', 'send_enabled': False}))
        return 0
    args.run, args.preflight, args.operator_idle = False, True, False
    try:
        if not args.synthetic_evidence or not args.virtual_desktop:
            raise ConsoleError('context_evidence_and_desktop_required')
        require_context_canary(args.synthetic_evidence)
        report = original.run(args, taskview_desktop_id=args.virtual_desktop,
                              inspection_worker=message_auxiliary_worker if args.inspect_message_auxiliary else
                              message_schema_worker if args.inspect_message_schema else
                              send_context_worker if args.inspect_send_context else
                              client_first_worker if args.client_first else inspection_worker)
    except Exception as exc:
        report = {'state': 'failed', 'reason': str(exc) if isinstance(exc, ConsoleError)
                  else 'readonly_context_preparation_failed', 'attempted': False}
    report.update(kind='bound_readonly_uia_context', send_invoked=False, api_called=False,
                  production_enabled=False,
                  client_first_requested=args.client_first or args.inspect_send_context or
                      args.inspect_message_schema or args.inspect_message_auxiliary)
    report['send_context_requested'] = args.inspect_send_context or args.inspect_message_schema or args.inspect_message_auxiliary
    report['message_schema_requested'] = args.inspect_message_schema or args.inspect_message_auxiliary
    report['message_auxiliary_requested'] = args.inspect_message_auxiliary
    path = original.DIRECTORY / ('uia-bound-context-' + uuid.uuid4().hex + '.json')
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'path':str(path), 'state':report['state'], 'reason':report.get('reason'),
                      'attempted':report['attempted'], 'patch_restored':report.get('patch_restored')}))
    return 0 if report['state'] == 'preflight_verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
