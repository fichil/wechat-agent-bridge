"""Read-only, exact-image UIA context evidence from a caller's own objects.

The injected port may read only the current process. No native call, internal
method invocation, pointer write, COM activation or process access occurs here.
Unknown layouts stop before following their fields. Output contains relations
and module RVAs, never pointer values or text from the inspected elements.
"""
import struct

from scripts.uia_setfocus_evidence import DLL_SHA256

CLIENT_TABLES = frozenset((0x2E3E40, 0x2E40D0, 0x2EC440))
ELEMENT_TABLE, PATTERN_TABLE, NODE_TABLE = 0x2DB360, 0x2E5EF0, 0x2DF4F8
INVOKE_PATTERN_TABLE = 0x2EB250
DELEGATING_NODE_TABLE = 0x2DFFD8
DELEGATE_TABLE = 0x2E38D0
MAX_READ_BYTES = 4096


class ContextError(ValueError):
    def __init__(self, reason, evidence=None):
        super().__init__(reason)
        self.evidence = dict(evidence or {})


def valid_range(address, count):
    return (type(address) is int and type(count) is int and
            0x10000 <= address < 0x800000000000 and 0 < count <= 128 and
            address + count <= 0x800000000000)


def readable_region(address, count, region, *, image_base=None):
    """Scalar VirtualQuery result; no guessing across allocation boundaries."""
    if not valid_range(address, count) or not isinstance(region, dict):
        return False
    try:
        start, size, protect = (region[k] for k in ('base', 'size', 'protect'))
        return bool(all(type(v) is int for v in region.values()) and
            start <= address and address + count <= start + size and size > 0 and
            region['state'] == 0x1000 and not protect & 0x100 and
            protect in (2, 4, 8, 0x20, 0x40, 0x80) and
            region['type'] in (0x20000, 0x1000000) and
            (image_base is None or
             (region['type'] == 0x1000000 and region['allocation'] == image_base)))
    except (KeyError, TypeError):
        return False


class Reader:
    def __init__(self, port, image):
        self.port, self.image, self.bytes_read = port, image, 0
        self.observed, self.evidence = {}, {'tables': {}, 'internal_methods_called': False}
        if (not isinstance(image, dict) or image.get('sha256') != DLL_SHA256 or
                type(image.get('base')) is not int or type(image.get('size')) is not int or
                not 0 < image['size'] <= 16 * 1024**2 or
                not valid_range(image['base'], 8) or
                image['base'] + image['size'] > 0x800000000000):
            self.fail('image_fingerprint_mismatch')

    def fail(self, reason):
        self.evidence['bytes_read'] = self.bytes_read
        raise ContextError(reason, self.evidence)

    def read(self, address, count=8, *, image=False):
        if count not in (4, 8) or not valid_range(address, count):
            self.fail('invalid_memory_range')
        if image and not (self.image['base'] <= address and
                          address + count <= self.image['base'] + self.image['size']):
            self.fail('image_range_mismatch')
        if self.bytes_read + count > MAX_READ_BYTES:
            self.fail('memory_read_limit')
        self.bytes_read += count
        try:
            raw = self.port.read(address, count, image=image)
        except Exception:
            self.fail('memory_read_failed')
        if type(raw) is not bytes or len(raw) != count:
            self.fail('memory_read_incomplete')
        key = (address, count, image)
        if key in self.observed and self.observed[key] != raw:
            self.fail('object_changed_during_read')
        self.observed[key] = raw
        return struct.unpack('<Q' if count == 8 else '<I', raw)[0]

    def table(self, pointer, label, allowed, slot, method):
        table = self.read(pointer)
        rva = table - self.image['base']
        if 0 <= rva < self.image['size']:
            self.evidence['tables'][label] = rva
        if rva not in allowed:
            self.fail(label + '_vtable_unverified')
        if self.read(table + slot, image=True) != self.image['base'] + method:
            self.fail(label + '_method_mismatch')

    def client(self, pointer, label):
        self.table(pointer, label, CLIENT_TABLES, 0x1D0, 0x1AC360)
        flag = self.read(pointer + 0x364, 4)
        if flag not in (0, 1, 0xFFFFFFFF):
            self.fail('client_flag_invalid')
        return flag != 0

    def finish(self):
        for (address, count, image), _ in tuple(self.observed.items()):
            self.read(address, count, image=image)
        self.evidence.update(stable=True, bytes_read=self.bytes_read)
        return self.evidence


def inspect_context(port, image, client, element, pattern, public_auto_focus,
                    *, pattern_kind='value'):
    """Follow only the pattern/element supplied by a verified public UIA caller.

    The caller pins those COM objects and checks their semantic identity before
    and after inspection. A NULL association is an observed layout value; this
    function does not claim which branch a future action would actually take.
    """
    read = Reader(port, image)
    # The exact-image Invoke entry 0x1c2180, named by its diagnostic string,
    # reads its parent element at +0x50 before calling 0x226940. This is a
    # separate layout proof, not an assumption that all patterns share it.
    if type(pattern_kind) is not str or pattern_kind not in ('value', 'invoke'):
        read.fail('pattern_kind_unverified')
    pattern_table, pattern_method = ((PATTERN_TABLE, 0x1C6670)
        if pattern_kind == 'value' else (INVOKE_PATTERN_TABLE, 0x1C2180))
    if type(public_auto_focus) is not bool:
        read.fail('public_flag_invalid')
    actual = read.client(client, 'requested_client')
    if actual != public_auto_focus:
        read.fail('client_flag_readback_mismatch')
    read.evidence['requested_auto_focus'] = actual
    read.table(element, 'element', {ELEMENT_TABLE}, 0x18, 0x2298E0)
    read.table(pattern, 'pattern', {pattern_table}, 0x18, pattern_method)
    if read.read(pattern + 0x50) != element:
        read.fail('pattern_element_mismatch')
    node = read.read(element + 0x38)
    node_table = read.read(node) - image['base']
    if node_table == DELEGATING_NODE_TABLE:
        # Exact-image tail thunk 0x15f740 reads this+0x28 and dispatches that
        # delegate's vtable+0x28. Do not apply the fresh-node +0x20 layout here.
        read.table(node, 'node', {DELEGATING_NODE_TABLE}, 0x30, 0x15F740)
        delegate = read.read(node + 0x28)
        if delegate == node:
            read.fail('node_delegate_cycle')
        read.table(delegate, 'delegated_node', {DELEGATE_TABLE}, 0x28, 0x120450)
        # Exact getter 0x120450 returns [this+0x170] with AddRef if nonNULL.
        # We read that scalar, never execute the getter or AddRef internally.
        association = read.read(delegate + 0x170)
    else:
        read.table(node, 'node', {NODE_TABLE}, 0x30, 0x15B790)
        association = read.read(node + 0x20)
    read.evidence['association'] = 'present' if association else 'null'
    if association:
        body = read.read(association + 0x10)
        associated_client = read.read(body + 0x38)
        associated_flag = read.client(associated_client, 'associated_client')
        read.evidence.update(associated_auto_focus=associated_flag,
            matches_requested_client=associated_client == client,
            matches_requested_context_body=body == client + 0x2E0)
    return read.finish()
