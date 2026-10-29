"""Narrow account/config readers adapted from the pinned Apache-2.0 reference.

No memory dumps, key files, GUI calls, or cross-PID key fallbacks.
"""
from pathlib import Path
import re
import struct

from .models import ProbeError

CONFIG_NAME = b'com.Tencent.WCDB.Config.Cipher'
XOR_MASK = bytes.fromhex('d2c7442458020000004889442450488b450048844c2448488944254048584c24')
IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{6,128}\Z')


def pointer(memory, address):
    data = memory.read(address, 8)
    return struct.unpack('<Q', data)[0] if len(data) == 8 else 0


def remote_string(memory, address):
    obj = memory.read(address, 32)
    if len(obj) != 32:
        return None
    size, capacity = struct.unpack_from('<QQ', obj, 16)
    if size == 0 and capacity <= 0x100000:
        return ''
    if not 0 < size <= 128 or capacity < size or capacity > 0x100000:
        return None
    data = obj[:size] if capacity <= 15 else memory.read(struct.unpack_from('<Q', obj)[0], size)
    if len(data) != size:
        return None
    try:
        value = data.decode('utf-8')
    except UnicodeError:
        return None
    return value if IDENTIFIER.fullmatch(value) else None


def current_account(memory, paths):
    """Read only cfg.wxId. Do not read the config's master-key field.

    The known 4.1 layout must also match the same PID's mapped account directory;
    anchor drift never falls back to an arbitrary string or newest directory.
    """
    modules = [(name, base) for name, base in paths.items()
               if Path(name).name.casefold() == 'weixin.dll']
    if len(modules) != 1:
        raise ProbeError('account_configuration_module_missing')
    _, base = modules[0]
    cached = getattr(memory, 'configuration_anchors', None)
    bounds = getattr(memory, 'account_module_bounds', None)
    if isinstance(cached, tuple) and bounds and bounds[0] == base:
        return account_at_anchors(memory, cached)
    header = memory.read(base, 4096)
    if len(header) != 4096 or header[:2] != b'MZ':
        raise ProbeError('account_configuration_module_invalid')
    pe = struct.unpack_from('<I', header, 0x3c)[0]
    if pe > 3500 or header[pe:pe+4] != b'PE\0\0':
        raise ProbeError('account_configuration_module_invalid')
    image_size = struct.unpack_from('<I', header, pe+0x50)[0]
    if not 0 < image_size < 512 * 1024 * 1024:
        raise ProbeError('account_configuration_module_invalid')
    memory.account_module_bounds = (base, base+image_size)
    marker = b'global_config'
    found = memory.find([marker, CONFIG_NAME], bounds=(base, base+image_size))
    memory.config_name_literals = found[CONFIG_NAME]
    anchors = []
    for address in found[marker]:
        metadata = memory.read(address+16, 16)
        if len(metadata) != 16 or struct.unpack('<QQ', metadata) != (13, 15):
            continue
        anchors.append(address)
    if not anchors:
        raise ProbeError('account_configuration_missing_or_ambiguous')
    value = account_at_anchors(memory, tuple(anchors))
    memory.configuration_anchors = tuple(anchors)
    return value


def account_at_anchors(memory, anchors):
    values = set()
    for address in anchors:
        owner = pointer(memory, address + 16 - 0x138)
        cfg = pointer(memory, owner + 0x68) if owner else 0
        if not 0x10000 <= cfg < 0x800000000000:
            continue
        value = remote_string(memory, cfg + 0x48)
        if value is not None:
            values.add(value)
    if len(values) != 1:
        raise ProbeError('account_configuration_missing_or_ambiguous')
    return values.pop()


def mapped_account_directories(paths):
    directories = set()
    for name in paths:
        path = Path(name)
        for parent in (path, *path.parents):
            if parent.name.casefold() == 'db_storage':
                directories.add(parent.parent)
                break
    return directories


def bind_directory(paths, wxid):
    roots = mapped_account_directories(paths)
    matches = [root for root in roots if root.name == wxid or
               (root.name.startswith(wxid+'_') and len(root.name) == len(wxid)+5)]
    if len(matches) != 1:
        raise ProbeError('account_directory_missing_or_ambiguous')
    return matches[0]


def contact_key(memory, first_page):
    """Compatibility wrapper; key lookup is shared by contact and avatar DBs."""
    return database_key(memory, first_page, check=memory.check)


def database_key(memory, first_page, check=lambda: None):
    """Cache candidates only inside this PID's short-lived read worker."""
    from .sqlcipher import verify_page
    check()
    candidates = getattr(memory, 'database_key_candidates', None)
    if not isinstance(candidates, set):
        names = getattr(memory, 'config_name_literals', ())
        if not names:
            names = memory.find([CONFIG_NAME], check=check)[CONFIG_NAME]
        candidates = candidates_at_names(memory, names, check)
        memory.database_key_candidates = candidates
    verified = verify_candidates(candidates, first_page, verify_page, check)
    if not verified and getattr(memory, 'database_candidates_fully_scanned', False) is not True:
        # Other builds may allocate the config name outside the main DLL.
        names = memory.find([CONFIG_NAME], check=check)[CONFIG_NAME]
        candidates.update(candidates_at_names(memory, names, check))
        memory.database_candidates_fully_scanned = True
        verified = verify_candidates(candidates, first_page, verify_page, check)
    if len(verified) != 1:
        raise ProbeError('database_key_missing_or_ambiguous')
    return verified.pop()


def verify_candidates(candidates, first_page, verify_page, check=lambda: None):
    verified = set()
    for key, salt in candidates:
        check()
        if verify_page(key, first_page[:16], first_page, 1):
            verified.add((key, first_page[:16]))
        elif salt is not None and verify_page(key, salt, first_page, 1):
            verified.add((key, salt))
    return verified


def candidates_at_names(memory, names, check=lambda: None):
    if not names:
        return set()
    patterns = {struct.pack('<QQ', address, len(CONFIG_NAME)) for address in names}
    nodes = memory.find(list(patterns), private_only=True, check=check)
    candidates = set()
    for addresses in nodes.values():
        for address in addresses:
            check()
            node = memory.read(address-16, 0x50)
            if len(node) != 0x50:
                continue
            config = struct.unpack_from('<Q', node, 0x28)[0]
            obj = memory.read(config+0x88, 0x28)
            if len(obj) != 0x28:
                continue
            data_pointer, size = struct.unpack_from('<QQ', obj, 8)
            if not 0 < size <= 1024:
                continue
            blob = memory.read(data_pointer, size)
            if len(blob) != size:
                continue
            decoded = bytes(value ^ XOR_MASK[i % len(XOR_MASK)] for i, value in enumerate(blob))
            for match in re.finditer(rb"[xX]'([0-9a-fA-F]{64,192})'", decoded):
                text = match.group(1)
                starts = {0, len(text)-64} | set(range(0, len(text)-63, 32))
                for start in starts:
                    key = bytes.fromhex(text[start:start+64].decode('ascii'))
                    if len(key) != 32:
                        continue
                    candidates.add((key, None))
                    if start+96 <= len(text):
                        salt = bytes.fromhex(text[start+64:start+96].decode('ascii'))
                        candidates.add((key, salt))
                    if len(candidates) > 2048:
                        raise ProbeError('database_key_candidates_exceeded')
    return candidates
