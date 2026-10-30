"""Exact builds only. Acceptance is separate from binary compatibility."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".local/native-send"
DLL = BUILD / "bridge_text.dll"
ACCEPTANCE = ROOT / ".local/native-send-acceptance.json"
REQUIRED_CHECKS = ('native_receipt', 'archive_readback', 'account_binding', 'module_unload',
                   'foreground', 'window_state', 'input', 'clipboard', 'drafts')
CORE_HASHES = {
    "e0428a7b691853de496532523c2c09aa095f90941c37c9f4cfb3f137f3c66556",
    "932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99",
}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build_identity():
    manifest = json.loads((BUILD / "build.json").read_text(encoding="utf-8-sig"))
    if digest(DLL) != manifest["dll_sha256"]:
        raise ValueError("native_build_changed")
    for name in ("text_bridge.cpp", "core_version.h"):
        if digest(ROOT / "native_send" / name) != manifest["sources"][name]:
            raise ValueError("native_build_stale")
    return manifest["dll_sha256"]


def accepted(account, core_hash, dll_hash):
    try:
        data = json.loads(ACCEPTANCE.read_text(encoding="utf-8"))
        return (data.get("passed") is True and data.get("dll_sha256") == dll_hash
                and data.get("core_sha256") == core_hash
                and all(data.get('checks', {}).get(name) == 'passed' for name in REQUIRED_CHECKS)
                and account in data.get("accounts", []))
    except (OSError, ValueError):
        return False
