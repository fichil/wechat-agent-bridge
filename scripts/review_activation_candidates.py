"""Disk-only review of activation candidates; never proof of an executed path.

The inputs are the exact DLL and already observed callback RVAs. This tool has
no process handles, writes to the client, or mechanism to enable live sending.
Capstone is needed only for explicit command-line analysis, not helper tests.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechat_agent_bridge.adapters.accessibility_gate import DLL_SHA256
from wechat_agent_bridge.adapters.pe_inventory import PE
from wechat_agent_bridge.adapters.static_types import vtable_slot


# Research addresses only, not an adapter profile or a callable interface.
FUNCTIONS = (
    0x3E39190, 0x9182E0, 0x1C43BC0, 0x1C43BD0, 0x1C48680,
    0x3EEC100, 0x5C2310, 0x5C1FE0, 0x5C99F0, 0x5C92B0,
    0x5C9EB0, 0x6B2630, 0x5C7FB0, 0x5BAFC0, 0x6B1780,
    0x5C86D0, 0x2F6760, 0x263BF70,
)
# These leaf boundaries are inspected explicitly. Never infer every leaf's
# boundary by stopping at its first return or unconditional jump: earlier
# conditional branches may have reachable code after that instruction.
LEAF_ENDS = {0x9182E0: 0x9182F2, 0x1C43BC0: 0x1C43BCC,
             0x1C43BD0: 0x1C43BDE}


def direct_transfers(instructions, begin, end):
    """Return literal external call/jump candidates, including conditional tails.

    Records are synthetic-testable. Reachability and branch feasibility are
    deliberately not claimed by this function.
    """
    result = []
    for ins in instructions:
        op, args = ins["op"], ins["args"]
        if op != "call" and not op.startswith("j"):
            continue
        if not re.fullmatch(r"0x[0-9a-fA-F]+", args):
            continue
        destination = int(args, 16)
        if begin <= destination < end:
            continue
        result.append({"site": ins["rva"], "op": op, "target": destination,
                       "conditional": op not in ("call", "jmp")})
    return result


def first_argument_evidence(instructions, index):
    """Recognize a local constant-zero RCX assignment, with no CFG inference.

    Stops at another control transfer and trusts explicit decoder write sets.
    This only rejects a tempting path; it cannot certify a non-null path.
    """
    for ins in reversed(instructions[max(0, index-8):index]):
        if ins["op"] in ("call", "ret") or ins["op"].startswith("j"):
            break
        if not {"rcx", "ecx"}.intersection(ins.get("writes", ())):
            continue
        args = [p.strip() for p in ins["args"].split(",")]
        is_zero = (ins["op"] == "xor" and len(args) == 2
                   and args[0] == args[1] and args[0] in ("rcx", "ecx"))
        is_zero |= (ins["op"] == "mov" and len(args) == 2
                    and args[0] in ("rcx", "ecx") and args[1] in ("0", "0x0"))
        return {"state": "constant_zero" if is_zero else "unknown",
                "assignment_site": ins["rva"]}
    return {"state": "unknown"}


def inspect_file(dll):
    sys.path.insert(0, str(ROOT/".local/static-tools"))
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64, __version__
    if __version__ != "5.0.6":
        raise ValueError("research_tool_version_mismatch")
    data = dll.read_bytes()
    if hashlib.sha256(data).hexdigest() != DLL_SHA256:
        raise ValueError("unreviewed_dll_fingerprint")
    pe = PE(data)
    image_base = pe.unpack("<Q", pe.directories-88)[0]
    pdata, size = pe.unpack("<II", pe.directories+24)
    bounds = dict(pe.unpack("<III", pe.offset(pdata+i, 12))[:2]
                  for i in range(0, size, 12))
    imports = {item["iat_rva"]: item["name"]
               for item in pe.imports()+pe.delay_imports()}
    disasm = Cs(CS_ARCH_X86, CS_MODE_64)
    disasm.detail = True
    records = []
    for begin in FUNCTIONS:
        end = bounds.get(begin, LEAF_ENDS.get(begin))
        if end is None or not 0 < end-begin < 32768:
            raise ValueError("unreviewed_function_boundary")
        instructions = []
        for ins in disasm.disasm(data[pe.offset(begin):pe.offset(end-1)+1], begin):
            item = {"rva": ins.address, "op": ins.mnemonic, "args": ins.op_str,
                    "writes": [ins.reg_name(reg) for reg in ins.regs_access()[1]]}
            if ins.bytes[:2] in (b"\xff\x15", b"\xff\x25") and ins.size == 6:
                slot = ins.address+6+struct.unpack("<i", ins.bytes[2:])[0]
                item["import"] = imports.get(slot)
            instructions.append(item)
        if not instructions:
            raise ValueError("empty_function")
        transfers = direct_transfers(instructions, begin, end)
        by_site = {ins["rva"]: i for i, ins in enumerate(instructions)}
        for edge in transfers:
            edge["first_argument"] = first_argument_evidence(instructions, by_site[edge["site"]])
        records.append({"begin": begin, "end": end,
                        "boundary_source": "pdata" if begin in bounds else "reviewed_leaf",
                        "transfers": transfers, "instructions": instructions})
    activation_slot = vtable_slot(pe, image_base, 0x94A98E8, 0xF8//8)
    if activation_slot["target"] != 0x263BF70:
        raise ValueError("activation_vtable_mismatch")
    label = pe.string(0x8FC7950)
    if label != "requestActivate() called for ":
        raise ValueError("activation_label_mismatch")
    return {"sha256": DLL_SHA256, "functions": records,
            "activation_slot": activation_slot, "request_activate_label": label,
            "runtime_platform_instance_bound": False, "execution_proven": False,
            "write_permission": False,
            "rejected_shortcut": {
                "site": 0x6B2E44, "callee": 0x6B1780,
                "reason": "RCX is zero at this call; the callee activation helper is on its non-null branch. Function-level reachability does not establish a feasible path."},
            "limitation": "Selected on-disk function evidence only. Runtime object binding, state branches, reentrant events and queued receiver correlation remain unproved."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dll", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.dll.resolve().parent in args.output.resolve().parents:
        parser.error("output_must_not_modify_installation")
    result = inspect_file(args.dll)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"reviewed_functions": len(result["functions"]),
                      "execution_proven": False, "write_permission": False}))


if __name__ == "__main__":
    main()
