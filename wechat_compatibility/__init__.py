"""Reviewed exact-build profiles shared by readers, sender and packaging."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import struct

REGISTRY = Path(__file__).with_name('profiles.json')
CAPABILITIES = ('account', 'messages', 'images', 'stickers', 'send_text')
NATIVE_FIELDS = ('application', 'app_vtable', 'context_vtable', 'app_get',
                 'context_get', 'scheduler_get', 'schedule', 'text_builder', 'allocate', 'free')
LAYOUT_FIELDS = ('anchor_owner_back', 'app_context', 'account_string', 'image_seed',
                 'app_get_slot', 'context_get_slot')
CIPHER_FIELDS = ('node_back', 'node_size', 'config_pointer', 'key_object', 'object_size', 'data_pointer')


@lru_cache(maxsize=1)
def registry():
    value = json.loads(REGISTRY.read_text(encoding='utf-8'))
    try:
        return validate_registry(value)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('compatibility_registry_incomplete') from exc


def validate_registry(value):
    if value.get('schema') != 1:
        raise ValueError('compatibility_registry_invalid')
    if not isinstance(value.get('profiles'), list) or not value['profiles']:
        raise ValueError('compatibility_registry_incomplete')
    ids, hashes, native_ids = set(), set(), set()
    for item in value['profiles']:
        if item['id'] in ids or item['arch'] != 'x64' or set(item['capabilities']) != set(CAPABILITIES):
            raise ValueError('compatibility_registry_invalid')
        ids.add(item['id'])
        if any(type(v) is not bool for v in item['capabilities'].values()):
            raise ValueError('compatibility_registry_invalid')
        if any(item['capabilities'].values()) and set(item['layout']) != set(LAYOUT_FIELDS):
            raise ValueError('compatibility_layout_incomplete')
        if any(item['capabilities'].values()):
            cipher = item.get('cipher_layout', {})
            if (item.get('read_schema') != 'wcdb-4.1' or item.get('media_schema') != 'dat-v1-v2' or
                    set(cipher) != {*CIPHER_FIELDS, 'config_name', 'xor_mask'} or
                    cipher['config_name'] != 'com.Tencent.WCDB.Config.Cipher' or
                    not 1 <= len(bytes.fromhex(cipher['xor_mask'])) <= 64 or
                    any(type(cipher[n]) is not int or not 0 <= cipher[n] < 4096 for n in CIPHER_FIELDS) or
                    cipher['config_pointer'] + 8 > cipher['node_size'] or
                    cipher['data_pointer'] + 16 > cipher['object_size'] or
                    any(type(v) is not int or not 0 <= v < 4096 for v in item['layout'].values())):
                raise ValueError('compatibility_read_layout_invalid')
        native = item['native']
        if type(native['id']) is not int or not 1 <= native['id'] < 65536 or native['id'] in native_ids:
            raise ValueError('compatibility_native_id_invalid')
        native_ids.add(native['id'])
        if item['capabilities']['send_text']:
            if not item['capabilities']['account'] or not item['capabilities']['messages']:
                raise ValueError('compatibility_readback_required')
            if set(item['native']['rvas']) != set(NATIVE_FIELDS) or item['native']['abi'] != 'msvc-x64-text-v1':
                raise ValueError('compatibility_native_incomplete')
            if not 0 <= item['layout']['account_string'] <= 127:
                raise ValueError('compatibility_native_signature_invalid')
        if not isinstance(item['builds'], list) or not item['builds']:
            raise ValueError('compatibility_build_invalid')
        for build in item['builds']:
            digest = build['sha256']
            if digest in hashes or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('compatibility_registry_invalid')
            hashes.add(digest)
            if (any(type(build[k]) is not int or build[k] <= 0 for k in ('bytes','timestamp','image_size')) or
                    any(type(v) is not int or not 0 < v < build['image_size']
                        for v in (*native['rvas'].values(), native['app_entry']))):
                raise ValueError('compatibility_build_invalid')
    return value


def profile_for_hash(digest):
    return next((p for p in registry()['profiles'] if any(b['sha256'] == digest for b in p['builds'])), None)


def profile_digest(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@lru_cache(maxsize=16)
def _file_identity(path, size, modified):
    with Path(path).open('rb') as source:
        head = source.read(4096)
        if head[:2] != b'MZ':
            raise ValueError('client_binary_invalid')
        pe = struct.unpack_from('<I', head, 60)[0]
        if not 64 <= pe <= 3500 or head[pe:pe+4] != b'PE\0\0':
            raise ValueError('client_binary_invalid')
        machine = struct.unpack_from('<H', head, pe+4)[0]
        timestamp = struct.unpack_from('<I', head, pe+8)[0]
        image_size = struct.unpack_from('<I', head, pe+80)[0]
        source.seek(0)
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    after = Path(path).stat()
    if (after.st_size, after.st_mtime_ns) != (size, modified):
        raise ValueError('client_binary_changed')
    profile = profile_for_hash(digest)
    if profile:
        build = next(b for b in profile['builds'] if b['sha256'] == digest)
        if machine != 0x8664 or (size, timestamp, image_size) != (build['bytes'], build['timestamp'], build['image_size']):
            raise ValueError('client_binary_invalid')
    return dict(core_sha256=digest, profile_id=profile['id'] if profile else None,
                timestamp=timestamp, image_size=image_size, machine=machine)


def file_identity(path):
    path = Path(path).resolve()
    value = path.stat()
    return dict(_file_identity(str(path), value.st_size, value.st_mtime_ns))


def require_profile(instance, capability):
    from wechat_account_probe.models import ProbeError
    profile = profile_for_hash(getattr(instance, 'core_sha256', ''))
    if (not profile or profile['version'] != instance.version or
            not profile['capabilities'].get(capability, False)):
        raise ProbeError('client_version_unsupported')
    return profile


def bind_memory(memory, paths, capability='account'):
    """Validate the mapped file and image before interpreting any account layout."""
    from wechat_account_probe.models import ProbeError
    try:
        found = [(p, base) for p, base in paths.items() if Path(p).name.lower() == 'weixin.dll']
        if len(found) != 1:
            raise ValueError('account_configuration_module_missing')
        path, base = found[0]
        identity = file_identity(path)
        if identity['core_sha256'] != memory.instance.core_sha256:
            raise ValueError('client_binary_changed')
        profile = require_profile(memory.instance, capability)
        header = memory.read(base, 4096)
        pe = struct.unpack_from('<I', header, 60)[0]
        if (header[:2] != b'MZ' or not 64 <= pe <= 3500 or header[pe:pe+4] != b'PE\0\0' or
                struct.unpack_from('<I', header, pe+8)[0] != identity['timestamp'] or
                struct.unpack_from('<I', header, pe+80)[0] != identity['image_size']):
            raise ValueError('client_binary_changed')
        memory.compatibility_profile = profile
        return profile
    except (OSError, ValueError, struct.error) as exc:
        raise ProbeError(str(exc) if isinstance(exc, ValueError) else 'client_binary_unreadable') from None


def layout(memory):
    return memory.compatibility_profile['layout']
