"""Bounded static PE metadata. Never loads libraries or calls their exports."""
import hashlib
import struct


class PE:
    def __init__(self, data):
        self.data = data
        if data[:2] != b'MZ':
            raise ValueError('not_pe')
        pe = self.unpack('<I', 0x3c)[0]
        if data[pe:pe+4] != b'PE\0\0':
            raise ValueError('invalid_pe')
        self.machine, count = self.unpack('<HH', pe+4)
        optional_size = self.unpack('<H', pe+20)[0]
        magic = self.unpack('<H', pe+24)[0]
        if magic not in (0x10b, 0x20b) or not 0 < count <= 96:
            raise ValueError('unsupported_pe')
        self.width = 8 if magic == 0x20b else 4
        self.sections = []
        self.directories = pe+24+(112 if self.width == 8 else 96)
        directory_count = self.unpack('<I', self.directories-4)[0]
        self.directory_count = directory_count
        if directory_count < 2 or self.directories+16 > pe+24+optional_size:
            raise ValueError('missing_directories')
        for i in range(count):
            at = pe+24+optional_size+40*i
            size, rva, raw_size, raw = self.unpack('<IIII', at+8)
            if raw+raw_size > len(data):
                raise ValueError('truncated_section')
            self.sections.append((rva, raw_size, raw))

    def unpack(self, fmt, at):
        if at < 0 or at+struct.calcsize(fmt) > len(self.data):
            raise ValueError('out_of_bounds')
        return struct.unpack_from(fmt, self.data, at)

    def offset(self, rva, size=1):
        for start, length, raw in self.sections:
            if start <= rva and rva+size <= start+length:
                return raw+rva-start
        raise ValueError('unmapped_rva')

    def string(self, rva):
        at = self.offset(rva)
        end = self.data.find(b'\0', at, min(at+2048, len(self.data)))
        if end < 0:
            raise ValueError('unterminated_name')
        return self.data[at:end].decode('ascii', errors='replace')

    def exports(self):
        rva, size = self.unpack('<II', self.directories)
        if not rva:
            return []
        at = self.offset(rva, 40)
        base, count, names, functions, name_table, ordinals = self.unpack('<IIIIII', at+16)
        if names > 100000 or count > 100000 or names > count:
            raise ValueError('excessive_exports')
        result = []
        for i in range(names):
            name_rva = self.unpack('<I', self.offset(name_table+4*i, 4))[0]
            ordinal = self.unpack('<H', self.offset(ordinals+2*i, 2))[0]
            if ordinal >= count:
                raise ValueError('invalid_export_ordinal')
            function = self.unpack('<I', self.offset(functions+4*ordinal, 4))[0]
            result.append({'name': self.string(name_rva), 'ordinal': base+ordinal,
                           'rva': function, 'forwarder': self.string(function) if rva <= function < rva+size else None})
        return result

    def imports(self):
        rva, size = self.unpack('<II', self.directories+8)
        if not rva:
            return []
        result = []
        for i in range(min(size//20, 4096)):
            lookup, stamp, forward, name, address = self.unpack('<IIIII', self.offset(rva+20*i, 20))
            if not any((lookup, stamp, forward, name, address)):
                return result
            module = self.string(name)
            table = lookup or address
            for j in range(65536):
                value = self.unpack('<Q' if self.width == 8 else '<I', self.offset(table+self.width*j, self.width))[0]
                if not value:
                    break
                symbol = '#'+str(value & 65535) if value >> (self.width*8-1) else self.string(value+2)
                result.append({'module': module, 'name': symbol, 'iat_rva': address+self.width*j})
            else:
                raise ValueError('unterminated_imports')
        raise ValueError('unterminated_descriptors')

    def delay_imports(self):
        if self.directory_count <= 13:
            return []
        rva, size = self.unpack('<II', self.directories+13*8)
        if not rva:
            return []
        result = []
        for i in range(min(size//32, 4096)):
            values = self.unpack('<IIIIIIII', self.offset(rva+32*i, 32))
            if not any(values):
                return result
            attrs, name, module, iat, names, bound, unload, stamp = values
            if attrs != 1:
                raise ValueError('unsupported_delay_va_descriptor')
            for j in range(65536):
                value = self.unpack('<Q' if self.width == 8 else '<I', self.offset(names+self.width*j, self.width))[0]
                if not value:
                    break
                symbol = '#'+str(value & 65535) if value >> (self.width*8-1) else self.string(value+2)
                result.append({'module': self.string(name), 'name': symbol, 'iat_rva': iat+self.width*j})
            else:
                raise ValueError('unterminated_delay_imports')
        raise ValueError('unterminated_delay_descriptors')


def inventory(data):
    pe = PE(data)
    return {'sha256': hashlib.sha256(data).hexdigest(), 'machine': pe.machine,
            'exports': pe.exports(), 'imports': pe.imports(), 'delay_imports': pe.delay_imports(),
            'limitations': 'Static named exports and normal/delay imports only; not API or compatibility proof.'}
