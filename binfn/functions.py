"""bytea and bit string functions and operators (PostgreSQL names)."""
import base64

from .values import BinError, Bits


def bytea_in(text):
    """text -> bytea (the type input function, escape or hex form)"""
    if text.startswith("\\x"):
        return bytes.fromhex(text[2:])
    return text.encode("utf-8")


def encode(data, fmt):
    if fmt == "hex":
        return data.hex()
    if fmt == "base64":
        return base64.b64encode(data).decode()
    if fmt == "escape":
        return "".join(chr(b) if 32 <= b < 127 else "\\x%02x" % b for b in data)
    raise BinError(f'unrecognized encoding: "{fmt}"')


def decode(text, fmt):
    if fmt == "hex":
        return bytes.fromhex(text)
    if fmt == "base64":
        return base64.b64decode(text)
    if fmt == "escape":
        return text.encode("latin-1")
    raise BinError(f'unrecognized encoding: "{fmt}"')


def get_byte(data, n):
    return str(data[n])


def set_byte(data, n, value):
    b = bytearray(data)
    b[n] = value
    return bytes(b)


def get_bit(data, n):
    if isinstance(data, Bits):
        return data[n]
    return str((data[n // 8] >> (7 - n % 8)) & 1)


def set_bit(data, n, value):
    if isinstance(data, Bits):
        return Bits(data[:n] + str(value) + data[n + 1:])
    b = bytearray(data)
    mask = 1 << (7 - n % 8)
    b[n // 8] = (b[n // 8] | mask) if value else (b[n // 8] & ~mask)
    return bytes(b)


def bit_count(data):
    if isinstance(data, Bits):
        return str(data.count("1"))
    return str(sum(bin(b).count("1") for b in data))


def length(data):
    return str(len(data))


def bit_length(data):
    return str(len(data) * 8) if isinstance(data, bytes) else str(len(data))


def substr(data, start, count=None):
    begin = max(start - 1, 0)
    return data[begin:] if count is None else data[begin:start - 1 + count]


def overlay(data, new, start, count=None):
    count = len(new) if count is None else count
    return data[:start - 1] + new + data[start - 1 + count:]


def btrim(data, chars):
    return data.strip(chars)


def concat(a, b):
    out = a + b
    return Bits(out) if isinstance(a, Bits) else out


def bit_cast(bits, n):
    """bit string -> bit(n)"""
    return Bits(bits.zfill(n)[-n:])


def _int(bits):
    return int(bits, 2) if bits else 0


def _bits(value, width):
    return Bits(format(value, "b").zfill(width))


def bit_and(a, b):
    return _bits(_int(a) & _int(b), max(len(a), len(b)))


def bit_or(a, b):
    return _bits(_int(a) | _int(b), max(len(a), len(b)))


def bit_xor(a, b):
    return _bits(_int(a) ^ _int(b), max(len(a), len(b)))


def bit_not(a):
    return Bits("".join("1" if c == "0" else "0" for c in a))


def shift_left(a, n):
    return _bits(_int(a) << n, len(a))


def shift_right(a, n):
    return _bits(_int(a) >> n, len(a))


FUNCTIONS = {
    "bytea": bytea_in, "encode": encode, "decode": decode, "get_byte": get_byte, "set_byte": set_byte,
    "get_bit": get_bit, "set_bit": set_bit, "bit_count": bit_count, "length": length,
    "bit_length": bit_length, "substr": substr, "overlay": overlay, "btrim": btrim, "||": concat,
    "bit": bit_cast, "&": bit_and, "|": bit_or, "#": bit_xor, "~": bit_not, "<<": shift_left, ">>": shift_right,
}
