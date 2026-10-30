"""Exact builds only. Acceptance is separate from binary compatibility."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from bridge_paths import DATA, NATIVE
BUILD = NATIVE
DLL = BUILD / "bridge_text.dll"
ACCEPTANCE = DATA / "native-send-acceptance.json"
REQUIRED_CHECKS = ('native_receipt', 'archive_readback', 'account_binding', 'module_unload',
                   'foreground', 'window_state', 'input', 'clipboard', 'drafts')
from wechat_compatibility import REGISTRY, registry, profile_for_hash, profile_digest
CORE_HASHES = {b['sha256'] for p in registry()['profiles'] if p['capabilities']['send_text'] for b in p['builds']}


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
    if manifest.get('compatibility_sha256') != digest(REGISTRY):
        raise ValueError('native_build_stale')
    return manifest["dll_sha256"]


def accepted(account, core_hash, dll_hash):
    try:
        data = json.loads(ACCEPTANCE.read_text(encoding="utf-8"))
        if data.get('version') == 2:
            profile = profile_for_hash(core_hash)
            if not profile or not profile['capabilities']['send_text']:
                return False
            binding = machine_binding()
            return any(record.get('account') == account and record.get('binding') == binding
                       and record.get('core_sha256') == core_hash and record.get('dll_sha256') == dll_hash
                       and record.get('profile_digest') == profile_digest(profile)
                       and record.get('passed') is True
                       and all(record.get('checks', {}).get(n) == 'passed' for n in REQUIRED_CHECKS)
                       for record in data.get('records', []))
        # Legacy records remain valid for source checkouts only, never portable installs.
        bound = data.get('binding')
        binding_ok = bound == machine_binding() if bound else not (ROOT / 'portable-manifest.json').exists()
        return (binding_ok and data.get("passed") is True and data.get("dll_sha256") == dll_hash
                and data.get("core_sha256") == core_hash
                and all(data.get('checks', {}).get(name) == 'passed' for name in REQUIRED_CHECKS)
                and account in data.get("accounts", []))
    except (OSError, ValueError):
        return False


def machine_binding():
    """Bind acceptance to this Windows installation and current user SID."""
    import os
    import winreg
    from wechat_account_probe.native import handle_sid
    import ctypes as c
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography', 0,
                       winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        machine = winreg.QueryValueEx(key, 'MachineGuid')[0]
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = c.c_void_p
    kernel.CloseHandle.argtypes = [c.c_void_p]
    sid = handle_sid(kernel, kernel.GetCurrentProcess())
    version = digest(ROOT / 'portable-manifest.json') if (ROOT / 'portable-manifest.json').is_file() else 'source'
    return hashlib.sha256((machine + '|' + sid + '|' + version).encode()).hexdigest()
