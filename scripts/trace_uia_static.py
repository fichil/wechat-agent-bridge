"""Disassemble bounded on-disk UIA functions; never load the inspected DLL."""
import argparse
import bisect
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'.local/static-tools'))
from wechat_agent_bridge.adapters.pe_inventory import PE
from wechat_agent_bridge.adapters.accessibility_gate import DLL_SHA256


def main():
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64, __version__
    if __version__ != '5.0.6':
        raise RuntimeError('research_tool_version_mismatch')
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
    pdata, size = pe.unpack('<II', pe.directories+3*8)
    functions = [pe.unpack('<III', pe.offset(pdata+i, 12))[:2] for i in range(0, size, 12)]
    functions.sort()
    starts = [f[0] for f in functions]
    imports = {i['iat_rva']: i['module']+'!'+i['name'] for i in pe.imports()+pe.delay_imports()}
    labels = (b'virtual QWindowsUiaValueProvider::SetValue', b'virtual QWindowsUiaInvokeProvider::Invoke',
              b'virtual QWindowsUiaMainProvider::GetPropertyValue',
              b'virtual QString __cdecl mmui::accessible::EditAccessible::attributes(int, int *, int *) const',
              b'virtual void __cdecl mmui::accessible::TextEditAccessible::scrollToSubstring(int, int)')
    result = {'sha256': DLL_SHA256, 'functions': [], 'limitations': 'Static cross-references only; no live invocation or complete indirect call graph.'}
    disasm = Cs(CS_ARCH_X86, CS_MODE_64)
    for label in labels:
        raw = data.find(label+b'\0')
        if raw < 0:
            continue
        target = next(rva+raw-offset for rva, length, offset in pe.sections if offset <= raw < offset+length)
        references = []
        for rva, length, offset in pe.sections:
            section = data[offset:offset+length]
            for match in re.finditer(rb'\x48\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]....', section, re.DOTALL):
                address = rva+match.start()
                if address+7+struct.unpack('<i', match.group()[3:])[0] == target:
                    references.append(address)
        for address in references:
            index = bisect.bisect_right(starts, address)-1
            if index < 0:
                continue
            begin, end = functions[index]
            if not begin <= address < end or end-begin > 16384:
                continue
            lines = []
            for ins in disasm.disasm(data[pe.offset(begin):pe.offset(end-1)+1], begin):
                annotation = ''
                if ins.bytes[:2] in (b'\xff\x15', b'\xff\x25') and ins.size == 6:
                    destination = ins.address+6+struct.unpack('<i', ins.bytes[2:])[0]
                    annotation = imports.get(destination, '')
                lines.append({'rva': ins.address, 'op': ins.mnemonic, 'args': ins.op_str, 'import': annotation})
            result['functions'].append({'label': label.decode(), 'begin': begin, 'end': end, 'reference': address, 'instructions': lines})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'functions': [{k:v for k,v in f.items() if k != 'instructions'} for f in result['functions']]}))


if __name__ == '__main__':
    main()
