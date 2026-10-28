"""Read-only public-pattern inventory inside the already bound test chat.

No focus, selection, menu, scroll, default-action, write, send or clipboard
operation is invoked. Availability is never reported as operational support.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import probe_background_draft as original
from scripts.uia_client_bootstrap import prepare_client_elements, read_pattern_context, require_safe_context
from wechat_agent_bridge.console_models import ConsoleError

PATTERNS = ('Invoke', 'Value', 'Text', 'LegacyIAccessible', 'ObjectModel',
            'ExpandCollapse', 'Selection', 'SelectionItem', 'Scroll', 'ScrollItem', 'Toggle')


def pattern_inventory(element, uia):
    result = {}
    for name in PATTERNS:
        record = dict(state='unverified', action_executed=False)
        try:
            property_id = getattr(uia, 'UIA_Is' + name + 'PatternAvailablePropertyId')
            pattern_id = getattr(uia, 'UIA_' + name + 'PatternId')
            available = element.GetCurrentPropertyValue(property_id)
            if available is False or available == 0:
                record['state'] = 'unavailable'
            elif available is True or available == 1:
                record['state'] = 'available' if element.GetCurrentPattern(pattern_id) else 'unverified'
        except Exception:
            pass  # COM exception text can contain UI content.
        result[name] = record
    if result['LegacyIAccessible']['state'] == 'available':
        try:
            from wechat_agent_bridge.adapters.semantic_probe import inspect_legacy_origin
            legacy = element.GetCurrentPattern(uia.UIA_LegacyIAccessiblePatternId).QueryInterface(
                uia.IUIAutomationLegacyIAccessiblePattern)
            result['LegacyIAccessible']['origin'] = inspect_legacy_origin(legacy)
            default = legacy.CurrentDefaultAction
            result['LegacyIAccessible']['default_action'] = default if default in (
                '', 'Press', 'Click', 'SetFocus', '按下', '单击', '点击', '设置焦点') else 'other_or_localized'
        except Exception:
            result['LegacyIAccessible']['origin'] = dict(status='unverified')
    return result


def element_metadata(element, uia, pid):
    runtime = tuple(element.GetRuntimeId())
    if not runtime or element.CurrentProcessId != pid:
        raise ConsoleError('semantic_node_identity_mismatch')
    classname = element.CurrentClassName
    if not isinstance(classname, str) or not re.fullmatch(r'[A-Za-z_:][A-Za-z0-9_:]{0,127}', classname):
        classname = 'other'
    # comtypes represents a null HWND as None for virtual child controls.
    native_hwnd = element.CurrentNativeWindowHandle
    result = dict(runtime_id=list(runtime), class_name=classname,
                  control_type=int(element.CurrentControlType), native_hwnd=0 if native_hwnd is None else int(native_hwnd),
                  enabled=bool(element.CurrentIsEnabled), patterns=pattern_inventory(element, uia),
                  operational_support_verified=False)
    if tuple(element.GetRuntimeId()) != runtime or element.CurrentProcessId != pid:
        raise ConsoleError('semantic_node_identity_changed')
    return result


def unique_by_runtime(root, raw, uia, control_type, expected_runtime):
    items = root.FindAll(uia.TreeScope_Descendants,
        raw.CreatePropertyCondition(uia.UIA_ControlTypePropertyId, control_type))
    if not 0 <= items.Length <= 512:
        raise ConsoleError('semantic_node_limit')
    matches = []
    for index in range(items.Length):
        item = items.GetElement(index)
        if tuple(item.GetRuntimeId()) == tuple(expected_runtime): matches.append(item)
    if len(matches) != 1:
        raise ConsoleError('semantic_bound_row_missing_or_ambiguous')
    return matches[0]


def worker(pipe, fp, names, expected, write):
    sys.coinit_flags = 0
    try:
        if write: raise ConsoleError('semantic_inventory_write_forbidden')
        from scripts.probe_bound_uia_context import prepare_send_element, check_send_element
        from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
        prepared = prepare_client_elements(fp, expected)
        client, root, element, pattern, legacy, uia = prepared
        raw = client._client
        send = prepare_send_element(prepared, fp)
        backend = WindowsUIA()
        current = backend.target_control_probe(fp['pid'], fp['hwnd'], names)
        original.check_draft_target(expected, current)
        original._exchange(pipe, 'verified', dict(target=current, root_runtime_id=list(root.GetRuntimeId())))
        original._exchange(pipe, 'checkpoint', 'before_bound_semantic_inventory')
        require_safe_context(read_pattern_context(raw, element, pattern))
        row = unique_by_runtime(root, raw, uia, uia.UIA_ListItemControlTypeId, expected['session_runtime_id'])
        records = dict(input=element_metadata(element, uia, fp['pid']),
                       send=element_metadata(send[1], uia, fp['pid']),
                       selected_session=element_metadata(row, uia, fp['pid']))
        children = send[0].FindAll(uia.TreeScope_Descendants, raw.CreateTrueCondition())
        if not 0 <= children.Length <= 96: raise ConsoleError('semantic_input_area_limit')
        records['input_area'] = [element_metadata(children.GetElement(i), uia, fp['pid'])
                                 for i in range(children.Length)]
        condition = raw.CreatePropertyCondition(uia.UIA_NamePropertyId, '消息')
        for name in ('消息列表', 'Messages'):
            condition = raw.CreateOrCondition(condition, raw.CreatePropertyCondition(uia.UIA_NamePropertyId, name))
        condition = raw.CreateAndCondition(condition,
            raw.CreatePropertyCondition(uia.UIA_ControlTypePropertyId, uia.UIA_ListControlTypeId))
        lists = root.FindAll(uia.TreeScope_Descendants, condition)
        if lists.Length != 1: raise ConsoleError('semantic_message_list_ambiguous')
        listing = lists.GetElement(0)
        records['message_list'] = element_metadata(listing, uia, fp['pid'])
        items = listing.FindAll(uia.TreeScope_Children,
            raw.CreatePropertyCondition(uia.UIA_ControlTypePropertyId, uia.UIA_ListItemControlTypeId))
        if not 0 <= items.Length <= 512: raise ConsoleError('semantic_message_row_limit')
        records['message_tail'] = [element_metadata(items.GetElement(i), uia, fp['pid'])
                                   for i in range(max(0, items.Length - 5), items.Length)]
        check_send_element(send, fp, uia)
        original.check_draft_target(expected, backend.target_control_probe(fp['pid'], fp['hwnd'], names))
        original._exchange(pipe, 'checkpoint', 'after_bound_semantic_inventory')
        pipe.send(dict(kind='result', value=dict(state='preflight_verified', attempted=False,
            records=records, message_body_read=False, action_executed=False, legacy_loaded_before_client=legacy)))
    except Exception as exc:
        try:
            reason = str(exc) if isinstance(exc, ConsoleError) else 'semantic_inventory_failed_' + type(exc).__name__
            pipe.send(dict(kind='error', value=reason))
        except (OSError, EOFError): pass
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
    source = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    try:
        from scripts.probe_bound_uia_context import require_context_canary
        if not args.synthetic_evidence or not args.virtual_desktop:
            raise ConsoleError('context_evidence_and_desktop_required')
        require_context_canary(args.synthetic_evidence)
        report = original.run(args, taskview_desktop_id=args.virtual_desktop, inspection_worker=worker)
    except Exception as exc:
        report = dict(state='failed', reason=str(exc) if isinstance(exc, ConsoleError)
                      else 'semantic_inventory_preparation_failed', attempted=False)
    report.update(kind='bound_public_semantic_inventory', send_invoked=False, api_called=False,
                  inspector_sha256=source, inspector_unchanged=hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == source)
    if not report['inspector_unchanged']:
        report.update(state='failed', reason='semantic_inspector_changed')
    path = original.DIRECTORY / ('bound-semantics-' + uuid.uuid4().hex + '.json')
    with path.open('x', encoding='utf-8') as stream: json.dump(report, stream, indent=2)
    print(json.dumps(dict(path=str(path), state=report['state'], reason=report.get('reason'),
                         patch_restored=report.get('patch_restored'))))
    return 0 if report['state'] == 'preflight_verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
