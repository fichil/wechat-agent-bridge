"""Exact-build static anchors. Nothing here is an executable adapter profile."""
import argparse
import bisect
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'.local/static-tools'))
from wechat_agent_bridge.adapters.accessibility_gate import DLL_SHA256
from wechat_agent_bridge.adapters.pe_inventory import PE
from wechat_agent_bridge.adapters.static_types import qt_inheritance, vtable_slot


def main():
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64, __version__
    if __version__ != '5.0.6':
        raise ValueError('research_tool_version_mismatch')
    parser = argparse.ArgumentParser()
    parser.add_argument('--dll', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.dll.resolve().parent in args.output.resolve().parents:
        parser.error('output_must_not_modify_installation')
    data = args.dll.read_bytes()
    if hashlib.sha256(data).hexdigest() != DLL_SHA256:
        raise ValueError('unreviewed_dll_fingerprint')
    pe = PE(data)
    header = pe.unpack('<I', 0x3c)[0]
    base = pe.unpack('<Q', header+48)[0]
    inheritance = qt_inheritance(pe, base, 0x9050ad8)
    if [x['class'] for x in inheritance[:4]] != ['mmui::ChatInputField', 'mmui::VoiceInputField', 'mmui::XTextEdit', 'QTextEdit']:
        raise ValueError('chat_inheritance_anchor_mismatch')
    setter = vtable_slot(pe, base, 0x908f258, 12)
    scroll = vtable_slot(pe, base, 0x908f368, 15)
    if (setter['target'] != 0x9d5990 or scroll['target'] != 0x9d5a80
            or setter['col']['type_descriptor'] != scroll['col']['type_descriptor']
            or setter['col']['subobject_offset'] != 0 or scroll['col']['subobject_offset'] != 0x20):
        raise ValueError('accessible_vtable_anchor_mismatch')
    rva, size = pe.unpack('<II', pe.directories+24)
    functions = [pe.unpack('<III', pe.offset(rva+i, 12))[:2] for i in range(0, size, 12)]
    starts = [f[0] for f in functions]
    disasm = Cs(CS_ARCH_X86, CS_MODE_64)
    reports = []
    # Reviewed, exact-file locations only. Addresses are never called.
    targets = (0x1834c0, 0x9d54a0, 0x9d5990, 0x1e96de0, 0x26a1460, 0x3e39190)
    for target in targets:
        index = bisect.bisect_right(starts, target)-1
        if index < 0:
            raise ValueError('missing_function_boundary')
        begin, end = functions[index]
        if begin != target or end-begin > 16384:
            raise ValueError('unverified_function_boundary')
        instructions = []
        for instruction in disasm.disasm(data[pe.offset(begin):pe.offset(end-1)+1], begin):
            instructions.append({'rva': instruction.address, 'op': instruction.mnemonic, 'args': instruction.op_str})
        reports.append({'begin': begin, 'end': end, 'instructions': instructions})
    thunks = []
    for start in (0x26a7c80, 0x26a7cc0):
        rows = []
        for instruction in disasm.disasm(data[pe.offset(start, 80):pe.offset(start)+80], start):
            rows.append({'rva': instruction.address, 'op': instruction.mnemonic, 'args': instruction.op_str})
            if instruction.mnemonic in ('ret', 'jmp'):
                break
        if not rows or rows[-1]['op'] != 'jmp' or rows[-1]['args'] != '0x26a1460':
            raise ValueError('unverified_text_content_thunk')
        thunks.append({'begin': start, 'instructions': rows})
    report = {'sha256': DLL_SHA256, 'class_hierarchy': inheritance, 'setter': setter,
              'scroll_anchor': scroll, 'functions': reports, 'thunks': thunks,
              'runtime_binding': 'unverified_static_only', 'write_permission': False,
              'limitation': 'Signals, runtime receivers and indirect dispatch are not a complete call graph; no no-focus guarantee.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'classes': [x['class'] for x in inheritance], 'setter_rva': setter['target'],
                      'static_functions': len(reports), 'runtime_binding': report['runtime_binding'], 'write_permission': False}))


if __name__ == '__main__':
    main()
