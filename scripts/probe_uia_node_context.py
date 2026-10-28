"""Explicit self-process UIA context observation on owned private-desktop Edit.

Four independent clients: direct/tree lookup, default/FALSE AutoSetFocus. No
SetValue, Invoke, external PID, debugger, ETW or real client is accepted here.
The owned GUI fixture still prepares its own empty Edit and local focus.
"""
import argparse
import ctypes as c
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import probe_uia_autofocus_cross_process as cross
from scripts import probe_uia_autofocus_tree as tree
from scripts.uia_context_evidence import ContextError, inspect_context
from scripts.uia_context_native import SelfMemory

PHASES = ('direct_default', 'tree_default', 'direct_false', 'tree_false')
ORIGINAL_CLIENT, ORIGINAL_PROVIDER = cross.client_main, cross.provider_main
ORIGINAL_FINGERPRINTS = cross.fingerprints


def fingerprints():
    value = ORIGINAL_FINGERPRINTS()
    for name in ('scripts/probe_uia_node_context.py', 'scripts/uia_context_evidence.py',
                 'scripts/uia_context_native.py'):
        value[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    return value


def context_phase(create, uia, target, exchange, name, records=None):
    before = exchange('prepare', name)
    if before.get('focus') != target['b']:
        raise RuntimeError('private_focus_baseline_unavailable')
    report = {'phase': name, 'focus_before': before['focus'], 'read_only_uia': True,
              'setvalue_called': False, 'invoke_called': False,
              'lookup': 'tree' if name.startswith('tree') else 'direct'}
    if records is not None:
        records.append(report)
    expected, input_id = tree.binding(create, uia, target)
    client = create(uia.CUIAutomation8, interface=uia.IUIAutomation2)
    if name.endswith('_false'):
        client.AutoSetFocus = 0
    public_flag = int(client.AutoSetFocus) != 0
    report['public_auto_focus'] = public_flag
    if public_flag == name.endswith('_false'):
        raise RuntimeError('requested_client_configuration_mismatch')
    if report['lookup'] == 'tree':
        root, matches, element, pattern = tree.tree_pattern(client, uia, target, expected, input_id)
    else:
        root, element = client.ElementFromHandle(target['host']), client.ElementFromHandle(target['a'])
        if (root.CurrentProcessId != target['pid'] or root.CurrentNativeWindowHandle != target['host'] or
                tuple(root.GetRuntimeId()) != expected.root_runtime_id or
                tree.base.identity(element, target) != expected.input_runtime_id):
            raise RuntimeError('direct_input_identity_mismatch')
        pattern = element.GetCurrentPattern(uia.UIA_ValuePatternId).QueryInterface(uia.IUIAutomationValuePattern)
    if pattern.CurrentIsReadOnly or pattern.CurrentValue != '':
        raise RuntimeError('owned_input_not_empty_or_readonly')
    observed = exchange('check', name)
    if observed.get('focus') != target['b'] or observed.get('focus_events'):
        raise RuntimeError('discovery_changed_private_focus')
    memory = SelfMemory()
    try:
        try:
            report['context'] = inspect_context(memory, memory.image,
                c.cast(client, c.c_void_p).value, c.cast(element, c.c_void_p).value,
                c.cast(pattern, c.c_void_p).value, public_flag)
            report['context_status'] = 'verified_observation'
        except ContextError as exc:
            report.update(context_status='unverified', context_reason=str(exc), context=exc.evidence)
        memory.validate_source()
    finally:
        memory.close()
    report['identity_stable'] = (tree.base.identity(element, target) == expected.input_runtime_id and
        tuple(root.GetRuntimeId()) == expected.root_runtime_id and
        (int(client.AutoSetFocus) != 0) == public_flag)
    report['empty_before_and_after'] = pattern.CurrentValue == ''
    observed = exchange('check', name)
    if observed.get('focus') != target['b'] or observed.get('focus_events'):
        raise RuntimeError('inspection_changed_private_focus')
    report.update(exchange('complete', name))
    return report


def client_main(*args):
    with tree.replace_attributes(tree, PHASES=PHASES, tree_phase=context_phase):
        return ORIGINAL_CLIENT(*args)


def provider_main(*args):
    # Only the owned GUI's final empty-text check changes. No candidate runs.
    with (tree.replace_attributes(tree, PHASES=PHASES),
          tree.replace_attributes(tree.candidate, FIXED_DRAFT='')):
        return ORIGINAL_PROVIDER(*args)


def _source_qualified(result):
    if result.get('error') or result.get('mta_verified') is not True:
        return False
    phases, target = result.get('phases', []), result.get('target', {})
    if [p.get('phase') for p in phases] != list(PHASES):
        return False
    for p in phases:
        if (p.get('error') or p.get('read_only_uia') is not True or
                p.get('setvalue_called') is not False or p.get('invoke_called') is not False or
                p.get('context_status') != 'verified_observation' or
                p.get('context', {}).get('stable') is not True or
                type(p.get('public_auto_focus')) is not bool or
                p['public_auto_focus'] != (not p['phase'].endswith('_false')) or
                p.get('lookup') != ('tree' if p['phase'].startswith('tree') else 'direct') or
                p.get('focus_before') != target.get('b') or p.get('focus') != target.get('b') or
                p.get('focus_events') != [] or p.get('event_overflow') or
                any(p.get(k) is not True for k in ('identity_stable', 'empty_before_and_after',
                                                  'native_full_readback_equal'))):
            return False
    return True


def source_qualified(result):
    try:
        return _source_qualified(result)
    except (AttributeError, TypeError, KeyError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-synthetic', action='store_true')
    args = parser.parse_args(argv)
    if not args.run_synthetic:
        print(json.dumps({'state': 'disabled', 'live_client_verified': False}))
        return 0
    sources = fingerprints()
    with (tree.replace_attributes(cross, client_main=client_main, provider_main=provider_main,
                                  fingerprints=fingerprints),
          tree.replace_attributes(tree, PHASES=PHASES, source_qualified=source_qualified)):
        report = cross.run()
    report.update(kind='self_process_readonly_uia_context', setvalue_called=False,
                  invoke_called=False, external_process_memory_read=False,
                  actual_client_verified=False, context_collection_passed=report['passed'],
                  source_unchanged=sources == fingerprints())
    if not report['source_unchanged']:
        report['passed'] = report['context_collection_passed'] = False
    report['state'] = 'owned_context_verified' if report['passed'] else 'owned_context_unverified'
    path = ROOT / '.local/live-acceptance' / ('uia-node-context-' + uuid.uuid4().hex + '.json')
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'path': str(path), 'passed': report['passed'], 'state': report['state'],
                      'error': report.get('error')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
