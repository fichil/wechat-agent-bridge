"""Synthetic SQLite pages with SQLCipher's 80 reserved bytes; no real data."""
import hashlib
import hmac
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PAGE = 4096
KEY = bytes(range(32))
SALT = bytes(range(16))
WXID = 'wxid_synthetic_owner'


def varint(value):
    pieces = [value & 127]
    while value >> 7:
        value >>= 7
        pieces.insert(0, (value & 127) | 128)
    return bytes(pieces)


def record(values):
    types, body = [], bytearray()
    for value in values:
        if value is None:
            types.append(0)
        elif isinstance(value, int):
            types.append(1)
            body.extend(bytes([value]))
        elif isinstance(value, bytes):
            types.append(12 + 2 * len(value))
            body.extend(value)
        else:
            encoded = value.encode()
            types.append(13 + 2 * len(encoded))
            body.extend(encoded)
    serial = b''.join(varint(value) for value in types)
    return varint(len(serial)+1) + serial + body


def page(cells, first=False):
    image = bytearray(PAGE)
    offset, cursor = (100 if first else 0), PAGE-80
    image[offset] = 13
    struct.pack_into('>H', image, offset+3, len(cells))
    for index, values in enumerate(cells):
        payload = record(values)
        cell = varint(len(payload)) + varint(index+1) + payload
        cursor -= len(cell)
        image[cursor:cursor+len(cell)] = cell
        struct.pack_into('>H', image, offset+8+index*2, cursor)
    struct.pack_into('>H', image, offset+5, cursor)
    if first:
        image[:16] = b'SQLite format 3\0'
        image[16:24] = b'\x10\x00\x01\x01\x50\x40\x20\x20'
        for location, value in ((24, 1), (28, 2), (40, 1), (44, 4), (56, 1), (92, 1), (96, 3049001)):
            struct.pack_into('>I', image, location, value)
    return bytes(image)


def plain_database(alias='synthetic_account', nickname='合成昵称', extra=True):
    schema = [('table', 'contact', 'contact', 2,
               'CREATE TABLE contact(username TEXT,alias TEXT,nick_name TEXT)')]
    rows = [(WXID, alias, nickname)]
    if extra:
        rows.append(('wxid_other_contact', 'must_not_export', '其他联系人'))
    return page(schema, first=True) + page(rows)


def encrypt_page(plain, number, key=KEY, salt=SALT):
    iv = bytes([number]) * 16
    start = 16 if number == 1 else 0
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    encrypted = encryptor.update(plain[start:-80]) + encryptor.finalize()
    image = (salt if number == 1 else b'') + encrypted + iv
    mk = hashlib.pbkdf2_hmac('sha512', key, bytes(v ^ 58 for v in salt), 2, dklen=32)
    return image + hmac.new(mk, image[start:] + struct.pack('<I', number), hashlib.sha512).digest()


def encrypted_database(**kwargs):
    plain = plain_database(**kwargs)
    return b''.join(encrypt_page(plain[n:n+PAGE], n//PAGE+1) for n in range(0, len(plain), PAGE))


def avatar_database(rows):
    schema = [('table', 'head_image', 'head_image', 2,
               'CREATE TABLE head_image(username TEXT,md5 TEXT,image_buffer BLOB,update_time INTEGER)')]
    return page(schema, first=True) + page(rows)


def checksum(data, state=(0, 0)):
    a, b = state
    for x, y in struct.iter_unpack('<II', data):
        a = (a+x+b) & 0xffffffff
        b = (b+y+a) & 0xffffffff
    return a, b


def wal(frames):
    header = struct.pack('>IIIIII', 0x377f0682, 3007000, PAGE, 0, 123, 456)
    state = checksum(header)
    output = header + struct.pack('>II', *state)
    for number, commit, plain in frames:
        encrypted = encrypt_page(plain, number)
        first = struct.pack('>II', number, commit)
        state = checksum(first + encrypted, state)
        output += first + header[16:24] + struct.pack('>II', *state) + encrypted
    return output
