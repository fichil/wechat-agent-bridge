"""Bounded Qt 5 / MSVC x64 metadata decoding from file bytes only."""

import re
import struct


def find_qt_metaobjects(pe, image_base, class_name):
    """Resolve an exact class's static string header and metaobject references.

    Bounded searches in file bytes only; a name occurrence alone is not proof.
    Every result must decode the complete existing inheritance contract.
    """
    if not isinstance(class_name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_:]{0,127}', class_name):
        raise ValueError('invalid_qt_class_name')
    name = class_name.encode('ascii')
    data = pe.data
    def hits(needle, start=0, end=None, maximum=256):
        count, position = 0, start
        while True:
            position = data.find(needle, position, len(data) if end is None else end)
            if position < 0: return
            count += 1
            if count > maximum: raise ValueError('qt_metadata_search_limit')
            yield position
            position += 1
    def rva(offset, size=1):
        for start, length, raw in pe.sections:
            if raw <= offset and offset + size <= raw + length:
                return start + offset - raw
        raise ValueError('qt_metadata_unmapped_file_offset')
    matches = {}
    for text_at in hits(name + b'\0', maximum=64):
        for header in hits(struct.pack('<iII', -1, len(name), 0),
                           max(0, text_at - 131072), text_at, maximum=1024):
            if header + 24 > text_at or pe.unpack('<q', header + 16)[0] != text_at - header:
                continue
            try: strings = rva(header, 24)
            except ValueError: continue
            for reference in hits(struct.pack('<Q', image_base + strings), maximum=128):
                try:
                    meta = rva(reference - 8, 32)
                    chain = qt_inheritance(pe, image_base, meta)
                except (ValueError, UnicodeError, struct.error):
                    continue
                if chain[0]['class'] == class_name:
                    matches[meta] = chain
    return [matches[key] for key in sorted(matches)]


def qt_metaobject(pe, image_base, rva):
    parent, strings, data, metacall = pe.unpack('<QQQQ', pe.offset(rva, 32))
    strings -= image_base
    data -= image_base

    def string(index):
        if not 0 <= index < 4096:
            raise ValueError('qt_string_index_out_of_bounds')
        entry = strings+24*index
        ref, size, allocation = pe.unpack('<iII', pe.offset(entry, 24))
        displacement = pe.unpack('<q', pe.offset(entry+16, 8))[0]
        if ref != -1 or allocation != 0 or not 0 <= size <= 2047:
            raise ValueError('unsupported_qt_string')
        value = pe.string(entry+displacement)
        if len(value.encode('ascii')) != size:
            raise ValueError('qt_string_length_mismatch')
        return value

    header = pe.unpack('<14I', pe.offset(data, 56))
    if header[0] != 8:
        raise ValueError('unsupported_qt_metadata_revision')
    count, offset = header[4:6]
    if count > 256 or (count and offset < 14):
        raise ValueError('qt_methods_out_of_bounds')
    methods = []
    for index in range(count):
        name, argc, parameters, tag, flags = pe.unpack('<5I', pe.offset(data+4*(offset+index*5), 20))
        kind = {0: 'method', 4: 'signal', 8: 'slot', 12: 'constructor'}[flags & 12]
        methods.append({'name': string(name), 'argc': argc, 'kind': kind, 'flags': flags})
    property_count, property_offset = header[6:8]
    if property_count > 256 or (property_count and property_offset < 14):
        raise ValueError('qt_properties_out_of_bounds')
    properties = []
    for index in range(property_count):
        name, type_info, flags = pe.unpack('<3I', pe.offset(data + 4*(property_offset + index*3), 12))
        prop = {'name': string(name), 'flags': flags,
                'readable': bool(flags & 1), 'writable': bool(flags & 2)}
        if type_info & 0x80000000:
            prop['type_name'] = string(type_info & 0x7FFFFFFF)
        else:
            prop['builtin_type_id'] = type_info
        properties.append(prop)
    return {'rva': rva, 'class': string(header[1]), 'parent': parent-image_base if parent else None,
            'methods': methods, 'properties': properties,
            'metacall': metacall-image_base if metacall else None}


def qt_inheritance(pe, image_base, rva):
    result, seen = [], set()
    while rva is not None:
        if rva in seen or len(seen) >= 16:
            raise ValueError('qt_inheritance_cycle_or_limit')
        seen.add(rva)
        record = qt_metaobject(pe, image_base, rva)
        result.append(record)
        rva = record['parent']
    return result


def msvc_col(pe, rva):
    signature, offset, cd_offset, type_rva, hierarchy, self_rva = pe.unpack('<6I', pe.offset(rva, 24))
    if signature != 1 or self_rva != rva:
        raise ValueError('invalid_msvc_col')
    pe.offset(type_rva, 16)
    pe.offset(hierarchy, 16)
    return {'rva': rva, 'subobject_offset': offset, 'type_descriptor': type_rva,
            'hierarchy': hierarchy, 'construction_offset': cd_offset}


def vtable_slot(pe, image_base, table, slot):
    if slot < 0 or slot > 255:
        raise ValueError('vtable_slot_out_of_bounds')
    col_rva = pe.unpack('<Q', pe.offset(table-8, 8))[0]-image_base
    col = msvc_col(pe, col_rva)
    target = pe.unpack('<Q', pe.offset(table+slot*8, 8))[0]-image_base
    pe.offset(target)
    return {'table': table, 'slot': slot, 'target': target, 'col': col}
