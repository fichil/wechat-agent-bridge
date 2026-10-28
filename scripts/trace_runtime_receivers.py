"""Static edges from the callbacks observed in the approved metadata snapshot.

No target process access. A reachable activation function is a candidate path,
not proof that a branch, indirect call or queued receiver actually executed.
"""
import argparse
import bisect
from collections import deque
import hashlib
import json
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/".local/static-tools"))
from wechat_agent_bridge.adapters.pe_inventory import PE
from wechat_agent_bridge.adapters.accessibility_gate import DLL_SHA256

# 2026-10-23 protected runtime snapshot: text/cursor update receivers only.
CALLBACKS = (0x3e39190, 0x2126f80, 0x1c22db0, 0x930350, 0x48eb3e0,
             0x2126bd0, 0x1c22970, 0x2127340, 0x3de1f90, 0x1c15af0,
             0x1c43bc0, 0x1c43bd0)


def main():
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64, __version__
    if __version__ != "5.0.6": raise ValueError("research_tool_version_mismatch")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dll", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.dll.resolve().parent in args.output.resolve().parents:
        parser.error("output_must_not_modify_installation")
    data = args.dll.read_bytes()
    if hashlib.sha256(data).hexdigest() != DLL_SHA256: raise ValueError("unreviewed_dll")
    pe = PE(data)
    rva, size = pe.unpack('<II', pe.directories+24)
    bounds = dict(pe.unpack('<III', pe.offset(rva+i, 12))[:2] for i in range(0, size, 12))
    imports = {i['iat_rva']:i['name'] for i in pe.imports()+pe.delay_imports()}
    disasm = Cs(CS_ARCH_X86, CS_MODE_64)
    queue = deque((callback,[callback]) for callback in CALLBACKS)
    visited, reports, paths = set(), [], []
    deadline = time.monotonic()+20
    while queue and len(visited) < 3000 and time.monotonic() < deadline:
        begin, path = queue.popleft()
        if begin in visited: continue
        visited.add(begin)
        end = bounds.get(begin)
        leaf = end is None and begin in CALLBACKS
        if leaf: end = begin+64
        if end is None or end-begin > 16384: continue
        instructions = list(disasm.disasm(data[pe.offset(begin):pe.offset(end-1)+1],begin))
        if leaf:
            stop = next((i for i,x in enumerate(instructions) if x.mnemonic in ('ret','jmp')),None)
            if stop is None: continue
            instructions = instructions[:stop+1]
        edges, unresolved, activations = [], [], []
        for ins in instructions:
            if ins.mnemonic not in ('call','jmp'): continue
            if ins.bytes[:2] in (b'\xff\x15',b'\xff\x25') and ins.size == 6:
                iat = ins.address+6+struct.unpack('<i',ins.bytes[2:])[0]
                name = imports.get(iat)
                if name in ('SetForegroundWindow','SetFocus','BringWindowToTop','AttachThreadInput'):
                    activations.append({'site':ins.address,'api':name})
                continue
            if ins.op_str.startswith('0x'):
                dest = int(ins.op_str,16)
                if dest != begin and dest in bounds:
                    edges.append(dest)
            else: unresolved.append(ins.address)
        if activations: paths.append({'functions':path,'activation_calls':activations,'execution_proven':False})
        if begin in CALLBACKS or activations:
            reports.append({'begin':begin,'end':end,'edges':sorted(set(edges)),
                            'unresolved_indirect_sites':unresolved,'activation_calls':activations})
        if len(path) < 7:
            queue.extend((edge,path+[edge]) for edge in edges if edge not in visited)
    result = {'sha256':DLL_SHA256,'seed_callbacks':CALLBACKS,'visited':len(visited),
              'bounded_or_incomplete':True,'paths':paths,'functions':reports,
              'limitation':'Static direct-edge reachability only. Branch conditions, leaf thunks, indirect and queued calls remain unresolved.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'visited':len(visited),'candidate_paths':paths,'write_permission':False}))


if __name__ == '__main__': main()
