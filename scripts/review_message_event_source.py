"""Fixed-file Win32k SendMessage event review; no trace or window operation."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.review_focus_event_source import verified_pe, imported_operand

PROFILES = {
    "win32kbase.sys": {
        "sha256": "27cb5e74b2e05c3a2c10d00cc216285f9dca48854c6cfb57df5e4d3731ec9aa3",
        "functions": {0xE3870: 0xE3A26, 0xE4868: 0xE496D, 0x3C100: 0x3C157},
    },
    "win32kfull.sys": {
        "sha256": "94f00aabe76a4af3794e2134b806bf89e556b66814859c285de62173350a5173",
        "functions": {0x547D8: 0x55BB1, 0x54208: 0x547D2, 0x34B40: 0x35145},
    },
}
SCHEMA = (("CallbackCount", 3, 1), ("pqmsg", 16, 8), ("hwnd", 16, 8),
          ("WParam", 16, 8), ("LParam", 16, 8), ("message", 8, 4),
          ("inputReadyTimeMs", 8, 4), ("flags", 8, 4),
          ("pidReceiver", 8, 4), ("tidReceiver", 8, 4))
DESCRIPTOR = struct.pack("<HBBBBHQ", 60, 0, 21, 4, 1, 60, 0x400000000400000)


def checked_layout(event, pointer_bytes=8):
    """Reject a different TDH shape before deriving packed payload offsets."""
    if pointer_bytes != 8:
        raise ValueError("unreviewed_pointer_width")
    identity = tuple(event.get(key) for key in ("id", "version", "channel", "level", "opcode", "task_id", "keyword"))
    if identity != (60, 0, 21, 4, 1, 60, "0x400000000400000"):
        raise ValueError("event_identity_mismatch")
    fields = event.get("fields", ())
    if len(fields) != len(SCHEMA):
        raise ValueError("event_schema_mismatch")
    layout, offset = [], 0
    for field, (name, kind, size) in zip(fields, SCHEMA):
        if (field.get("name"), field.get("in_type"), field.get("count_or_index"),
            field.get("length_or_index"), field.get("top_level"), field.get("flags")) != (name, kind, 1, size, True, 0):
            raise ValueError("event_schema_mismatch")
        layout.append({"name": name, "offset": offset, "bytes": size})
        offset += size
    return {"bytes": offset, "pointer_bytes": pointer_bytes, "fields": layout}


def inspect_files(directory, metadata_file):
    sys.path.insert(0, str(ROOT/".local/static-tools"))
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64, __version__
    from capstone.x86_const import X86_OP_MEM, X86_REG_RIP
    if __version__ != "5.0.6":
        raise ValueError("research_tool_version_mismatch")
    metadata_bytes = metadata_file.read_bytes()
    providers = json.loads(metadata_bytes)["providers"]
    provider = next(p for p in providers if p["name"] == "Microsoft-Windows-Win32k")
    if provider["resource"]["sha256"] != PROFILES["win32kbase.sys"]["sha256"]:
        raise ValueError("metadata_resource_mismatch")
    event = next(event for event in provider["events"] if event["id"] == 60)
    layout = checked_layout(event)
    report = {"schema": 1, "kind": "fixed_driver_disk_only_sendmessage_review",
              "trace_started": False, "windows_operated": False,
              "kernel_memory_read": False, "runtime_emission_verified": False,
              "live_diagnostic_ready": False, "modules": [],
              "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest()}
    disasm = Cs(CS_ARCH_X86, CS_MODE_64)
    disasm.detail = True
    for name, profile in PROFILES.items():
        data = (directory/name).read_bytes()
        pe = verified_pe(data, profile["sha256"], profile["functions"])
        imports = {item["iat_rva"]: item["name"] for item in pe.imports()}
        exports = {item["rva"]: item["name"] for item in pe.exports()}
        module = {"name": name, "sha256": profile["sha256"], "functions": []}
        for begin, end in profile["functions"].items():
            lines = []
            for ins in disasm.disasm(data[pe.offset(begin):pe.offset(end-1)+1], begin):
                row = {"rva": ins.address, "op": ins.mnemonic, "args": ins.op_str}
                names = []
                for operand in ins.operands:
                    if operand.type == X86_OP_MEM and operand.mem.base == X86_REG_RIP:
                        imported = imported_operand(ins.address, ins.size, operand.mem.disp, imports)
                        if imported:
                            names.append(imported)
                if names:
                    row["imports"] = names
                lines.append(row)
            module["functions"].append({"begin": begin, "end": end,
                                        "export": exports.get(begin), "instructions": lines})
        if name == "win32kbase.sys":
            at = pe.offset(0x26E788, 16)
            if data[at:at+16] != DESCRIPTOR:
                raise ValueError("event_descriptor_mismatch")
            module["event_descriptor"] = {"rva": 0x26E788, "id": 60,
                "version": 0, "channel": 21, "level": 4, "opcode": 1,
                "task": 60, "keyword": "0x400000000400000"}
        report["modules"].append(module)
    report["findings"] = {
        "emit_call_in_queue_path": {"module": "win32kfull.sys", "function": 0x547D8,
                                    "site": 0x5560E, "callee": "EtwTraceBeginSendMessage"},
        "base_emit_path": [0xE3870, 0xE4868, 0x3C100],
        "normal_message_redaction": {"message_zero_site": 0xE3948,
            "lparam_zero_site": 0xE3953, "wparam_zero_site": 0xE395E,
            "exception_message": 0x112, "exception_wparam": 0xF170,
            "exception_must_not_be_used_as_stimulus": True},
        "receiver_tid_source": {"thread_pointer_offset": 0x28, "PsGetThreadId_call": 0xE390B},
        "receiver_pid_source": {"process_pointer_from_thread": 0x1D0,
                                "pid_field": 0x38, "read_site": 0xE393C},
        "x64_packed_payload": layout,
    }
    report["limits"] = [
        "No runtime kernel event was emitted or collected by this tool.",
        "No input-desktop/foreground comparison was found in these selected six functions; this is not an exhaustive upstream graph.",
        "An own-process source-only request/response must pass before an administrator trace.",
        "Do not filter ordinary WM_APP messages by event60.message: the source redacts it to zero.",
        "Receiver PID/TID integer filters require reciprocal positive/negative runtime controls.",
        "Event60 stack success would not prove event2 stack capture or identify WeChat activation.",
    ]
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--system-directory", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.system_directory.resolve() in args.output.resolve().parents:
        parser.error("output_must_not_modify_system_directory")
    report = inspect_files(args.system_directory, args.metadata)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"reviewed_functions": 6, "payload_bytes": 53,
                      "runtime_emission_verified": False, "live_diagnostic_ready": False}))


if __name__ == "__main__":
    main()
