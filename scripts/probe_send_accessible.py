"""Read only the bound send button's cached accessible interface and vtable.

No debugger, target method invocation, text read, whole-heap scan or new patch.
The existing controller owns the approved accessibility gate and restoration.
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
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.adapters.runtime_accessibility import interface_for_id

FOCUS_ONLY_ACTION_SHA256 = '2485e303d02e2a46a1f35e283fcc3a5db1f3986ae7a93ba8ce08081aa17eda31'
CLICKED_CALLBACK_SHA256 = '2b7ffaf70eeb8f8246d906d965c2026e727894d30834da0d65318320c13d8465'
SESSION_STATE_CODE = {
    0x9D0F50: (145, '6afb645c3f38d0e6917f5f5519fc20b64047fe0aab1e4f8a7610264851297619'),
    0x9D2080: (150, 'a10c8fbde544f55bd957aa98df413d9122943150d494a810265b3ebea765e13e'),
}


def read_selected_cell(memory, fp, profile, widget, state_rva, metadata):
    if state_rva != 0x9D0F50 or metadata.get('class') != 'mmui::ChatSessionCell':
        raise ConsoleError('session_selected_layout_unverified')
    if not any(item.get('rva') == 0x9073490 and item.get('class') == 'mmui::XTableCell'
               for item in metadata.get('hierarchy', [])):
        raise ConsoleError('session_selected_base_unverified')
    for rva, (count, digest) in SESSION_STATE_CODE.items():
        offset = profile.pe.offset(rva, count)
        code = profile.pe.data[offset:offset + count]
        if hashlib.sha256(code).hexdigest() != digest or memory.read(fp.module_base + rva, count, witness=True) != code:
            raise ConsoleError('session_selected_code_changed')
    # Exact state getter calls 9D2080, which qobject_casts to XTableCell's
    # verified metaobject and ORs selected bit 1 only for byte [cell+210h]==1.
    selected = memory.number(widget + 0x210, '<B')
    if selected != 1:
        raise ConsoleError('session_cell_not_selected')
    return dict(selected_state_layout_verified=True, selected_field_offset=0x210,
                selected=True, selected_state_mask=2, methods_called=False)


def inspect_clicked_capture(memory, profile, base, slot, callback, receiver):
    if callback != base + 0x1C231B0:
        return None
    offset = profile.pe.offset(0x1C231B0, 72)
    code = profile.pe.data[offset:offset + 72]
    if (hashlib.sha256(code).hexdigest() != CLICKED_CALLBACK_SHA256 or
            memory.read(callback, 72, witness=True) != code):
        raise ConsoleError('send_clicked_callback_code_changed')
    # Exact reviewed callback loads the capture from slot + 0x10 for Send's
    # this pointer. Compare with the actual connection receiver; never call it.
    if not receiver or memory.number(slot + 0x10) != receiver:
        raise ConsoleError('send_clicked_capture_receiver_mismatch')
    return dict(captured_receiver_matches_connection=True,
                loaded_callback_matches_file=True, callback_executed=False)


def verify_focus_only_action(memory, address, file_bytes):
    if len(file_bytes) != 0x7F or hashlib.sha256(file_bytes).hexdigest() != FOCUS_ONLY_ACTION_SHA256:
        raise ConsoleError('send_action_reviewed_code_mismatch')
    if memory.read(address, len(file_bytes), witness=True) != file_bytes:
        raise ConsoleError('send_action_loaded_code_changed')
    return dict(reviewed_action='focus_only', press_action_supported=False,
                loaded_action_matches_file=True, action_code_sha256=FOCUS_ONLY_ACTION_SHA256)


def inspect_button_connections(memory, fp, profile, interface, table):
    from wechat_agent_bridge.adapters.runtime_accessibility import object_metadata, connections
    # The same exact-file object getter used by the already reviewed input
    # interface. No C++ method is called; weak-object fields are read only.
    if memory.number(table + 0x10) != fp.module_base + 0x1B8FF00:
        raise ConsoleError('send_object_getter_unverified')
    weak = memory.number(interface + 8)
    control = memory.number(weak)
    if not control or memory.number(control + 4, '<i') == 0:
        raise ConsoleError('send_widget_destroyed')
    widget = memory.number(weak + 8)
    meta = object_metadata(memory, fp.module_base, fp.module_size, profile, widget)
    if meta['class'] != 'mmui::XOutlineButton':
        raise ConsoleError('send_widget_class_unverified')
    records = connections(memory, widget, fp.module_base, fp.module_size, profile,
        slot_inspector=lambda slot, callback, receiver:
            inspect_clicked_capture(memory, profile, fp.module_base, slot, callback, receiver))
    return dict(widget_class=meta['class'], widget_vtable_rva=meta['vtable_rva'],
                signal_connections=records, callbacks_executed=False)


def inspect_session_guard(memory, fp, profile, runtime):
    """Read only the already selected authorized row's cached interface."""
    from wechat_agent_bridge.adapters.runtime_accessibility import object_metadata
    interface = interface_for_id(memory, fp.module_base, runtime)
    table = memory.number(interface)
    if not fp.module_base <= table <= fp.module_base + fp.module_size - 160:
        raise ConsoleError('session_accessible_table_outside_module')
    slots = []
    for index in range(20):
        actual = memory.number(table + index * 8)
        expected = profile.pe.unpack('<Q', profile.pe.offset(table - fp.module_base + index * 8, 8))[0] - profile.base
        if not 0 <= expected < fp.module_size or actual != fp.module_base + expected:
            raise ConsoleError('session_accessible_vtable_changed')
        slots.append(expected)
    result = dict(accessible_vtable_rva=table-fp.module_base, slot_rvas=slots,
                  methods_called=False, selected_state_layout_verified=False)
    if slots[2] == 0x1B8FF00:
        weak = memory.number(interface + 8)
        control = memory.number(weak)
        if not control or memory.number(control + 4, '<i') == 0:
            raise ConsoleError('session_widget_destroyed')
        widget = memory.number(weak + 8)
        result['widget'] = object_metadata(memory, fp.module_base, fp.module_size, profile, widget)
        result.update(read_selected_cell(memory, fp, profile, widget, slots[15], result['widget']))
    if interface_for_id(memory, fp.module_base, runtime) != interface:
        raise ConsoleError('session_accessible_cache_changed')
    return result


def inspect_interface(memory, fp, profile, runtime, *, inspect_connections=False):
    profile.validate_loaded(memory, fp)
    interface = interface_for_id(memory, fp.module_base, runtime)
    table = memory.number(interface)
    if not fp.module_base <= table <= fp.module_base + fp.module_size - 160:
        raise ConsoleError('send_accessible_table_outside_module')
    table_rva = table - fp.module_base
    cast = memory.number(table + 0x98)
    file_cast = profile.pe.unpack('<Q', profile.pe.offset(table_rva + 0x98, 8))[0] - profile.base
    if cast != fp.module_base + file_cast or not 0 <= file_cast < fp.module_size:
        raise ConsoleError('send_accessible_cast_mismatch')
    result = dict(accessible_table_rva=table_rva, interface_cast_rva=file_cast,
                  runtime_binding='same_uia_send_node_cache_entry', execution_proven=False)
    # Exact current-file cast: ActionInterface(3) returns this + 0x10. This
    # offset is established by bytes, not inferred from an upstream C++ layout.
    cast_bytes = bytes.fromhex('4883c11031c083fa03480f44c1c3')
    if file_cast == 0x9D05E0:
        disk = profile.pe.offset(file_cast, len(cast_bytes))
        if (profile.pe.data[disk:disk+len(cast_bytes)] != cast_bytes or
                memory.read(cast, len(cast_bytes), witness=True) != cast_bytes):
            raise ConsoleError('send_accessible_cast_code_changed')
        action_table = memory.number(interface + 0x10)
        if not fp.module_base <= action_table <= fp.module_base + fp.module_size - 40:
            raise ConsoleError('send_action_table_outside_module')
        action_rva = action_table - fp.module_base
        action = memory.number(action_table + 0x20)
        disk_action = profile.pe.unpack('<Q', profile.pe.offset(action_rva + 0x20, 8))[0] - profile.base
        if action != fp.module_base + disk_action or not 0 <= disk_action < fp.module_size:
            raise ConsoleError('send_action_pointer_mismatch')
        result.update(action_table_rva=action_rva, do_action_rva=disk_action)
        if (table_rva, action_rva, disk_action) == (0x908CD08, 0x908CDB8, 0x9D0330):
            offset = profile.pe.offset(disk_action, 0x7F)
            result.update(verify_focus_only_action(memory, action, profile.pe.data[offset:offset+0x7F]))
    if interface_for_id(memory, fp.module_base, runtime) != interface:
        raise ConsoleError('send_accessible_cache_changed')
    if inspect_connections:
        if result.get('press_action_supported') is not False:
            raise ConsoleError('send_widget_reviewed_binding_required')
        result['connections'] = inspect_button_connections(memory, fp, profile, interface, table)
    memory.recheck()
    return result


def worker(pipe, fp_dict, names, expected, write, *, inspect_connections=False, inspect_session=False):
    sys.coinit_flags = 0
    native = reader = None
    try:
        if write:
            raise ConsoleError('send_accessible_write_forbidden')
        from scripts.uia_client_bootstrap import prepare_client_elements, read_pattern_context, require_safe_context
        from scripts.probe_bound_uia_context import prepare_send_element, check_send_element
        from wechat_agent_bridge.adapters.windows_uia import WindowsUIA
        from wechat_agent_bridge.adapters.accessibility_gate import NativeGate, Fingerprint
        from wechat_agent_bridge.adapters.runtime_accessibility import ExactProfile, ReadOnlyProcess, BoundedMemory
        prepared = prepare_client_elements(fp_dict, expected)
        send = prepare_send_element(prepared, fp_dict)
        backend = WindowsUIA()
        current = backend.target_control_probe(fp_dict['pid'], fp_dict['hwnd'], names)
        # Let the controller retain non-content mismatch metadata before it
        # rejects this worker. Its acknowledgement still requires the exact
        # selected target; no process memory is inspected before that gate.
        original._exchange(pipe, 'verified', dict(target=current, root_runtime_id=list(prepared[1].GetRuntimeId())))
        original.check_draft_target(expected, current)
        original._exchange(pipe, 'checkpoint', 'before_send_interface_metadata')
        require_safe_context(read_pattern_context(prepared[0]._client, prepared[2], prepared[3]))
        fp = Fingerprint(**fp_dict)
        native = NativeGate(fp.hwnd)
        if native.fingerprint() != fp:
            raise ConsoleError('send_accessible_identity_changed')
        profile = ExactProfile(native.path.read_bytes())
        reader = ReadOnlyProcess(fp, native.fingerprint)
        memory = BoundedMemory(reader.read, reader.check, max_bytes=32768)
        if inspect_connections or inspect_session:
            sys.path.insert(0, str(ROOT / '.local/static-tools'))
        result = inspect_interface(memory, fp, profile, list(send[3]), inspect_connections=inspect_connections)
        if inspect_session:
            result['session_guard'] = inspect_session_guard(memory, fp, profile, expected['session_runtime_id'])
            memory.recheck()
        check_send_element(send, fp_dict, prepared[-1])
        current = backend.target_control_probe(fp.pid, fp.hwnd, names)
        original.check_draft_target(expected, current)
        original._exchange(pipe, 'checkpoint', 'after_send_interface_metadata')
        result.update(state='preflight_verified', attempted=False, send_runtime_id=list(send[3]),
                      send_class=send[1].CurrentClassName, invoke_called=False,
                      process_memory_bytes_read=32768-memory.remaining)
        pipe.send(dict(kind='result', value=result))
    except Exception as exc:
        try:
            pipe.send(dict(kind='error', value=str(exc) if isinstance(exc, ConsoleError) else 'send_accessible_inspection_failed'))
        except (OSError, EOFError): pass
    finally:
        if reader: reader.close()
        if native: native.close()
        pipe.close()


def connections_worker(pipe, fp, names, expected, write):
    return worker(pipe, fp, names, expected, write, inspect_connections=True)


def session_worker(pipe, fp, names, expected, write):
    return worker(pipe, fp, names, expected, write, inspect_connections=True, inspect_session=True)


def inspector_sources():
    paths = ('scripts/probe_send_accessible.py', 'wechat_agent_bridge/adapters/runtime_accessibility.py',
             'wechat_agent_bridge/adapters/static_types.py', 'wechat_agent_bridge/adapters/pe_inventory.py')
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in paths}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--inspect-bound', action='store_true')
    parser.add_argument('--inspect-connections', action='store_true')
    parser.add_argument('--inspect-session', action='store_true')
    parser.add_argument('--allow-accessibility-patch', action='store_true')
    for flag in ('account', 'group', 'private', 'target-evidence', 'synthetic-evidence', 'virtual-desktop'):
        parser.add_argument('--' + flag)
    args = parser.parse_args(argv)
    if not args.inspect_bound:
        print(json.dumps(dict(state='disabled', send_enabled=False)))
        return 0
    args.run, args.preflight, args.operator_idle = False, True, False
    source_before = inspector_sources()
    try:
        from scripts.probe_bound_uia_context import require_context_canary
        if not args.synthetic_evidence or not args.virtual_desktop:
            raise ConsoleError('context_evidence_and_desktop_required')
        require_context_canary(args.synthetic_evidence)
        report = original.run(args, taskview_desktop_id=args.virtual_desktop,
            inspection_worker=session_worker if args.inspect_session else connections_worker if args.inspect_connections else worker)
    except Exception as exc:
        report = dict(state='failed', reason=str(exc) if isinstance(exc, ConsoleError)
                      else 'send_accessible_preparation_failed', attempted=False)
    report.update(kind='bound_send_accessible_metadata', send_invoked=False, api_called=False,
                  connections_requested=args.inspect_connections,
                  session_requested=args.inspect_session, inspector_sources=source_before,
                  inspector_sources_unchanged=inspector_sources() == source_before,
                  inspector_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    if not report['inspector_sources_unchanged']:
        report.update(state='failed', reason='send_inspector_source_changed')
    path = original.DIRECTORY / ('send-accessible-' + uuid.uuid4().hex + '.json')
    with path.open('x', encoding='utf-8') as stream: json.dump(report, stream, indent=2)
    print(json.dumps(dict(path=str(path), state=report['state'], reason=report.get('reason'),
                         patch_restored=report.get('patch_restored'))))
    return 0 if report['state'] == 'preflight_verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
