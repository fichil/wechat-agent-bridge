"""Own-source AutoSetFocus tree-path comparison; default disabled.

Reuses the frozen private non-input desktop canary without editing it. All
patterns in this comparison come from root.FindAll(Edit + AutomationId).
FALSE phases execute the real draft candidate on synthetic windows only.
The coexistence phase holds a raw legacy CUIAutomation reader, not pywinauto's
global caches. No live target, diagnostic allowance, send or provider API exists.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_uia_autofocus as base
from wechat_agent_bridge.adapters import uia_draft_candidate as candidate
from wechat_agent_bridge.adapters.uia_client import BackgroundUIAClient

BASE_HASH = '2bd0994cb22dcbba594e2cd266574c67a06903711e6da05acbfe77d919e14138'
PHASES = ('legacy_default_tree', 'modern_default_tree',
          'modern_no_focus_tree', 'modern_no_focus_legacy_reader_tree')
_ORIGINAL_CHILD, _ORIGINAL_FINGERPRINTS = base.child, base.fingerprints


@contextmanager
def replace_attributes(module, **values):
    """Only our disposable Python process; never modifies a DLL or file."""
    old = {name: getattr(module, name) for name in values}
    try:
        for name, value in values.items():
            setattr(module, name, value)
        yield
    finally:
        for name, value in old.items():
            setattr(module, name, value)


def require_base():
    actual = hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest()
    if actual != BASE_HASH:
        raise RuntimeError('frozen_autofocus_base_changed')


def fingerprints():
    result = _ORIGINAL_FINGERPRINTS()
    for name in ('scripts/probe_uia_autofocus_tree.py',
                 'wechat_agent_bridge/adapters/uia_draft_candidate.py'):
        result[name] = hashlib.sha256((base.ROOT / name).read_bytes()).hexdigest()
    return result


def binding(create, uia, target):
    """Bind exact owned HWNDs before traversing; retain only scalar identities."""
    client = BackgroundUIAClient(lambda: create(uia.CUIAutomation8, interface=uia.IUIAutomation2))
    root, edit = client.element_from_handle(target['host']), client.element_from_handle(target['a'])
    if root.CurrentProcessId != target['pid'] or root.CurrentNativeWindowHandle != target['host']:
        raise RuntimeError('synthetic_root_identity_mismatch')
    input_runtime = base.identity(edit, target)
    root_runtime = tuple(root.GetRuntimeId())
    if root_runtime == input_runtime or not 1 <= len(root_runtime) <= 16:
        raise RuntimeError('synthetic_root_runtime_invalid')
    # The frozen source explicitly creates A with native control ID 101.
    # Require the observed property, never silently substitute a missing ID.
    input_id = edit.CurrentAutomationId
    if input_id != '101' or edit.CurrentControlType != uia.UIA_EditControlTypeId:
        raise RuntimeError('synthetic_edit_automation_id_not_verified')
    return candidate.DraftTarget(target['host'], target['pid'], root_runtime, input_runtime), input_id


def tree_pattern(raw, uia, target, expected, input_id):
    root = raw.ElementFromHandle(target['host'])
    if (root.CurrentProcessId != expected.pid or root.CurrentNativeWindowHandle != expected.hwnd or
            tuple(root.GetRuntimeId()) != expected.root_runtime_id):
        raise RuntimeError('synthetic_root_changed')
    condition = raw.CreateAndCondition(
        raw.CreatePropertyCondition(uia.UIA_AutomationIdPropertyId, input_id),
        raw.CreatePropertyCondition(uia.UIA_ControlTypePropertyId, uia.UIA_EditControlTypeId))
    matches = root.FindAll(uia.TreeScope_Descendants, condition)
    if type(matches.Length) is not int or matches.Length != 1:
        raise RuntimeError('synthetic_tree_input_ambiguous')
    edit = matches.GetElement(0)
    if (base.identity(edit, target) != expected.input_runtime_id or
            edit.CurrentAutomationId != input_id or edit.CurrentControlType != uia.UIA_EditControlTypeId):
        raise RuntimeError('synthetic_tree_identity_changed')
    pattern = edit.GetCurrentPattern(uia.UIA_ValuePatternId).QueryInterface(uia.IUIAutomationValuePattern)
    return root, matches, edit, pattern


def tree_phase(create, uia, target, exchange, name, evidence=None):
    if name not in PHASES:
        raise RuntimeError('unknown_synthetic_tree_phase')
    before = exchange('prepare', name)
    if before.get('focus') != target['b']:
        raise RuntimeError('private_focus_baseline_unavailable')
    report = {'phase': name, 'fresh_client': True, 'attempted': False,
              'focus_before': before['focus'], 'pattern_path': 'root_findall_edit_id',
              'real_candidate_used': name in PHASES[2:], 'synthetic_claim_only': True,
              'legacy_reader_kind': 'raw_CUIAutomation' if name == PHASES[3] else 'none'}
    if evidence is not None:
        evidence.append(report)
    expected, input_id = binding(create, uia, target)
    report['synthetic_input_id'] = input_id
    report['runtime_id'] = list(expected.input_runtime_id)
    held_reader = None
    if name == PHASES[3]:
        old = create(uia.CUIAutomation, interface=uia.IUIAutomation)
        held_reader = (old, *tree_pattern(old, uia, target, expected, input_id))
        report['legacy_reader_retained'] = True
    check = exchange('check', name)
    if check.get('focus') != target['b'] or check.get('focus_events'):
        raise RuntimeError('synthetic_tree_discovery_changed_focus')

    if name in PHASES[:2]:
        modern = name == PHASES[1]
        raw = create(uia.CUIAutomation8 if modern else uia.CUIAutomation,
                     interface=uia.IUIAutomation2 if modern else uia.IUIAutomation)
        if modern:
            report['default_auto_set_focus'] = int(raw.AutoSetFocus)
            report['configured_auto_set_focus'] = int(raw.AutoSetFocus)
            if report['configured_auto_set_focus'] not in (1, -1):
                raise RuntimeError('synthetic_modern_default_not_true')
        root, matches, edit, pattern = tree_pattern(raw, uia, target, expected, input_id)
        if pattern.CurrentIsReadOnly or pattern.CurrentValue != '':
            raise RuntimeError('synthetic_tree_existing_draft')
        check = exchange('check', name)
        if check.get('focus') != target['b'] or check.get('focus_events'):
            raise RuntimeError('synthetic_tree_discovery_changed_focus')
        report['attempted'] = True
        pattern.SetValue(candidate.FIXED_DRAFT)
        report['uia_full_readback_equal'] = pattern.CurrentValue == candidate.FIXED_DRAFT
        report['identity_stable'] = (base.identity(edit, target) == expected.input_runtime_id and
                                     tuple(root.GetRuntimeId()) == expected.root_runtime_id)
    else:
        clients, claim_count, reader_checks = [], 0, 0

        def factory():
            raw = create(uia.CUIAutomation8, interface=uia.IUIAutomation2)
            clients.append(raw)  # Keep the candidate client through final policy/readback checks.
            report['default_auto_set_focus'] = int(raw.AutoSetFocus)
            return raw

        def checkpoint(stage):
            nonlocal reader_checks
            # Match the real candidate's legacy checkpoint reads, while using only
            # our scalar binding and exact owned root. No discovery at commit_write.
            if held_reader and stage not in ('commit_write', 'after_write'):
                _raw, root, _matches, edit, pattern = held_reader
                if (root.CurrentProcessId != expected.pid or root.CurrentNativeWindowHandle != expected.hwnd or
                        tuple(root.GetRuntimeId()) != expected.root_runtime_id or
                        base.identity(edit, target) != expected.input_runtime_id):
                    return False
                value = pattern.CurrentValue
                required = candidate.FIXED_DRAFT if stage == 'after_readback' else ''
                if value != required:
                    return False
                reader_checks += 1
            observed = exchange('check', name)
            return observed.get('focus') == target['b'] and observed.get('focus_events') == []

        def claim():
            nonlocal claim_count
            claim_count += 1
            report['synthetic_claim_count'] = claim_count
            return claim_count == 1

        # This constant is changed only inside this owned synthetic child and
        # restored even if the call fails. The production source file is untouched.
        with replace_attributes(candidate, INPUT_AUTOMATION_ID=input_id):
            outcome = candidate.write_fixed_draft(expected, enabled=True, checkpoint=checkpoint,
                claim=claim, client_factory=factory, bindings=uia)
        report.update(candidate_result=outcome, attempted=outcome['attempted'],
                      uia_full_readback_equal=outcome['readback_equal'] is True,
                      identity_stable=outcome['status'] == 'readback_confirmed',
                      legacy_reader_checks=reader_checks)
        if len(clients) != 1:
            raise RuntimeError('synthetic_candidate_client_count_mismatch')
        report['configured_auto_set_focus'] = int(clients[0].AutoSetFocus)
        if held_reader:
            report['legacy_reader_final_readback_equal'] = held_reader[-1].CurrentValue == candidate.FIXED_DRAFT
    report.update(exchange('complete', name))
    return report


def source_qualified(result):
    phases, target = result.get('phases', []), result.get('target', {})
    if (result.get('error') or result.get('mta_verified') is not True or
            [item.get('phase') for item in phases] != list(PHASES) or
            not target.get('a') or not target.get('b') or target['a'] == target['b']):
        return False
    for item in phases:
        if (item.get('error') or item.get('focus_before') != target['b'] or item.get('event_overflow') or
                item.get('pattern_path') != 'root_findall_edit_id' or item.get('synthetic_input_id') != '101' or
                not all(item.get(key) is True for key in ('fresh_client', 'attempted',
                    'uia_full_readback_equal', 'native_full_readback_equal', 'identity_stable'))):
            return False
    for item in phases[:2]:
        if item.get('focus') != target['a'] or [target['a'], 'set'] not in item.get('focus_events', []):
            return False
    if phases[1].get('configured_auto_set_focus') not in (1, -1):
        return False
    for item in phases[2:]:
        outcome = item.get('candidate_result', {})
        if (item.get('default_auto_set_focus') not in (1, -1) or item.get('configured_auto_set_focus') != 0 or
                item.get('focus') != target['b'] or item.get('focus_events') != [] or
                item.get('real_candidate_used') is not True or item.get('synthetic_claim_count') != 1 or
                outcome.get('status') != 'readback_confirmed' or outcome.get('claim_confirmed') is not True):
            return False
    return (phases[2].get('legacy_reader_kind') == 'none' and
            phases[3].get('legacy_reader_kind') == 'raw_CUIAutomation' and
            phases[3].get('legacy_reader_retained') is True and phases[3].get('legacy_reader_checks') == 6 and
            phases[3].get('legacy_reader_final_readback_equal') is True)


def tree_child(pipe, desktop_id):
    require_base()
    with replace_attributes(base, phase=tree_phase, PHASES=PHASES,
                            TEXT={name: candidate.FIXED_DRAFT for name in PHASES}):
        _ORIGINAL_CHILD(pipe, desktop_id)


def run():
    require_base()
    # Reuse desktop/GUI/timeout/cleanup unchanged; only own child target and
    # synthetic qualification differ. The previous report path is never used.
    with replace_attributes(base, child=tree_child, fingerprints=fingerprints, source_qualified=source_qualified):
        report = base.run()
    report.update(pattern_path='root_findall_edit_id', full_pywinauto_wrapper_verified=False,
                  live_allowance_touched=False, live_retry_authorized=False,
                  old_direct_canary_unmodified=report.get('source_unchanged') is True)
    report['state'] = 'synthetic_tree_source_verified' if report['passed'] else 'source_not_verified'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-synthetic', action='store_true')
    args = parser.parse_args(argv)
    if not args.run_synthetic:
        print(json.dumps({'state': 'disabled', 'passed': False, 'live_client_verified': False,
                          'live_allowance_touched': False, 'live_retry_authorized': False}))
        return 0
    try:
        report = run()
    except Exception as exc:
        report = {'state': 'source_not_verified', 'passed': False, 'live_client_verified': False,
                  'live_allowance_touched': False, 'live_retry_authorized': False,
                  'error': str(exc) if type(exc) is RuntimeError else type(exc).__name__}
    destination = base.ROOT / '.local/live-acceptance/uia-autofocus-tree-synthetic.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
