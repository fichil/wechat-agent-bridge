"""Review fixed on-disk Win32k event-2 sources, never load or execute them.

No process/driver/kernel-memory/ETW/window APIs. The addresses below are research
evidence for these exact hashes, not production interfaces or a support profile.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechat_agent_bridge.adapters.pe_inventory import PE

PROFILES = {
    "win32kbase.sys": {
        "sha256": "27cb5e74b2e05c3a2c10d00cc216285f9dca48854c6cfb57df5e4d3731ec9aa3",
        "file_version": "10.0.26100.9549",
        "functions": {0x11B2C0: 0x11B323, 0xA1F60: 0xA1FD3, 0x3C100: 0x3C157},
    },
    "win32kfull.sys": {
        "sha256": "94f00aabe76a4af3794e2134b806bf89e556b66814859c285de62173350a5173",
        "file_version": "10.0.26100.9711",
        "functions": {
            0x223F24: 0x224613, 0x2232A8: 0x22374D, 0x254A10: 0x2556EC,
            0x2ADE80: 0x2ADF10, 0x26D148: 0x26DEEC, 0x2987A4: 0x298822,
            0x2A5EB0: 0x2A5EE4, 0x2A5AA0: 0x2A5B5C, 0x28ECC0: 0x28ED8F,
        },
    },
}
EVENT_2 = struct.pack("<HBBBBHQ", 2, 0, 16, 4, 0, 2, 0x8000000000002000)


def verified_pe(data, expected_hash, expected_bounds):
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise ValueError("unreviewed_driver_fingerprint")
    pe = PE(data)
    if pe.machine != 0x8664 or pe.width != 8:
        raise ValueError("expected_x64_driver")
    pdata, size = pe.unpack("<II", pe.directories+24)
    if not size or size % 12 or size > 16*1024*1024:
        raise ValueError("invalid_unwind_directory")
    bounds = dict(pe.unpack("<III", pe.offset(pdata+i, 12))[:2]
                  for i in range(0, size, 12))
    for begin, end in expected_bounds.items():
        if bounds.get(begin) != end or not 0 < end-begin < 8192:
            raise ValueError("unreviewed_function_boundary")
        pe.offset(begin, end-begin)
    return pe


def event_descriptor(pe, rva):
    offset = pe.offset(rva, len(EVENT_2))
    if pe.data[offset:offset+len(EVENT_2)] != EVENT_2:
        raise ValueError("event_descriptor_mismatch")
    return {"rva": rva, "id": 2, "task": 2, "channel": 16, "level": 4,
            "keyword": "0x8000000000002000"}


def imported_operand(address, instruction_size, displacement, imports):
    """Use the decoded size, including a REX prefix, to resolve RIP imports."""
    return imports.get(address+instruction_size+displacement)


def inspect_directory(directory):
    sys.path.insert(0, str(ROOT/".local/static-tools"))
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64, __version__
    from capstone.x86_const import X86_OP_MEM, X86_REG_RIP
    if __version__ != "5.0.6":
        raise ValueError("research_tool_version_mismatch")
    disasm = Cs(CS_ARCH_X86, CS_MODE_64)
    disasm.detail = True
    report = {"schema": 1, "kind": "bounded_disk_static_review", "modules": [],
              "kernel_memory_read": False, "trace_started": False,
              "windows_operated": False, "runtime_execution_proven": False,
              "live_diagnostic_ready": False}
    for name, profile in PROFILES.items():
        data = (directory/name).read_bytes()
        pe = verified_pe(data, profile["sha256"], profile["functions"])
        imports = {item["iat_rva"]: item["name"] for item in pe.imports()}
        exports = {item["rva"]: item["name"] for item in pe.exports()}
        module = {"name": name, "sha256": profile["sha256"],
                  "file_version_for_hash": profile["file_version"],
                  "size": len(data), "functions": []}
        for begin, end in profile["functions"].items():
            instructions = []
            chunk = data[pe.offset(begin):pe.offset(end-1)+1]
            for ins in disasm.disasm(chunk, begin):
                row = {"rva": ins.address, "op": ins.mnemonic, "args": ins.op_str}
                names = []
                for operand in ins.operands:
                    if operand.type == X86_OP_MEM and operand.mem.base == X86_REG_RIP:
                        name_found = imported_operand(ins.address, ins.size,
                                                      operand.mem.disp, imports)
                        if name_found:
                            names.append(name_found)
                if names:
                    row["imports"] = names
                instructions.append(row)
            module["functions"].append({"begin": begin, "end": end,
                                        "export_name": exports.get(begin),
                                        "instructions": instructions})
        if name == "win32kbase.sys":
            module["event_descriptor"] = event_descriptor(pe, 0x26EFA0)
        report["modules"].append(module)
    report["findings"] = [
        {"claim": "event_2_existing_descriptor_and_write_path",
         "module": "win32kbase.sys", "function": 0x11B2C0,
         "descriptor": 0x26EFA0, "write_path": [0x11B31C, 0xA1FBB, 0x3C136],
         "final_import": "EtwWriteTransfer", "runtime_emission_verified": False},
        {"claim": "event_2_is_on_session_foreground_thread_transition",
         "function": 0x223F24, "event_call": 0x224111,
         "same_thread_early_return_comparison": 0x223F5F,
         "old_new_tid_source": "PsGetThreadId", "session_thread_offset": 0x4E70},
        {"claim": "local_focus_route_checks_foreground_queue",
         "function": 0x2232A8, "queue_comparison": 0x22345C,
         "conditional_skip": 0x223463, "update_call": 0x2234B0,
         "session_queue_offset": 0x4E60},
        {"claim": "reviewed_foreground_route_rejects_non_input_desktop",
         "function": 0x254A10, "input_desktop_load": 0x254ADC,
         "comparison": 0x254AE3, "mismatch_branch": 0x254AE7,
         "cleanup": 0x254C38, "zero_return": 0x254C5C,
         "session_input_desktop_offset": 0x4F68},
        {"claim": "input_desktop_offset_independently_identified",
         "export": "NtUserGetInputDesktop", "export_rva": 0x2A5EB0,
         "helper": 0x2987A4, "load": 0x2987BC, "session_offset": 0x4F68},
    ]
    report["limitations"] = [
        "Static source paths do not prove event emission, filter effectiveness or stack capture.",
        "Private non-input desktop local focus success is not an event-2 positive control.",
        "No claim that event 2 is deprecated or permanently disabled.",
        "No symbols downloaded, drivers loaded, kernel memory read or UI/trace operation performed.",
    ]
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--system-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.system_directory.resolve() in args.output.resolve().parents:
        parser.error("output_must_not_modify_system_directory")
    report = inspect_directory(args.system_directory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"reviewed_functions": sum(len(m["functions"]) for m in report["modules"]),
                      "runtime_execution_proven": False, "live_diagnostic_ready": False}))


if __name__ == "__main__":
    main()
