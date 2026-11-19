"""bytea and bit string functions and operators (PostgreSQL names)."""
from .values import (BinError, Bits, bytea_in, escape_decode, hex_decode,
                     type_name)

_B64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_B64_LOOKUP = {c: i for i, c in enumerate(_B64_ALPHABET)}


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _no_func(name, *args):
    raise BinError("function %s(%s) does not exist"
                   % (name, ", ".join(type_name(a) for a in args)))


def _no_oper(op, *args):
    raise BinError("operator does not exist: %s"
                   % (" %s " % op).join(type_name(a) for a in args))


def bytea(text):
    """text -> bytea (the type input function, escape or hex form)"""
    if isinstance(text, (bytes, bytearray)):
        return bytes(text)
    if not isinstance(text, str) or isinstance(text, Bits):
        _no_func("bytea", text)
    return bytea_in(text)


def _b64_encode(data):
    """PostgreSQL folds base64 output with a newline every 76 characters
    (including a trailing newline when the length is a multiple of 76)."""
    out = []
    line = 0
    n = len(data)
    for i in range(0, n, 3):
        c1 = data[i]
        c2 = data[i + 1] if i + 1 < n else 0
        c3 = data[i + 2] if i + 2 < n else 0
        out.append(_B64_ALPHABET[(c1 & 0xFC) >> 2])
        out.append(_B64_ALPHABET[((c1 << 4) | (c2 >> 4)) & 0x3F])
        out.append(_B64_ALPHABET[((c2 << 2) | (c3 >> 6)) & 0x3F])
        out.append(_B64_ALPHABET[c3 & 0x3F])
        if i + 3 >= n:
            # the last group may need padding
            if n % 3 == 1:
                out[-2] = "="
                out[-1] = "="
            elif n % 3 == 2:
                out[-1] = "="
        line += 4
        if line >= 76:
            out.append("\n")
            line = 0
    return "".join(out)


def _b64_decode(text):
    """PostgreSQL's pg_b64_decode(): whitespace is ignored, a '=' stops
    decoding (the input's own trailing newline may follow it), and a
    leftover group of two or three characters still decodes to one or
    two bytes."""
    out = bytearray()
    acc = 0
    nchars = 0
    c = ""
    for c in text:
        if c in " \t\n\r":
            continue
        if c == "=":
            # padding terminates the encoded data
            break
        v = _B64_LOOKUP.get(c)
        if v is None:
            raise BinError('invalid symbol "%s" found while decoding '
                           "base64 sequence" % c)
        pos = nchars % 4
        if pos == 0:
            acc = v << 18
        elif pos == 1:
            acc |= v << 12
        elif pos == 2:
            acc |= v << 6
        else:
            acc |= v
        nchars += 1
        if nchars % 4 == 0:
            out.extend(((acc >> 16) & 0xFF, (acc >> 8) & 0xFF, acc & 0xFF))
            acc = 0
    rem = nchars % 4
    if rem == 1:
        raise BinError('invalid symbol "%s" found while decoding '
                       "base64 sequence" % c)
    if rem == 2:
        out.append((acc >> 16) & 0xFF)
    elif rem == 3:
        out.extend(((acc >> 16) & 0xFF, (acc >> 8) & 0xFF))
    return bytes(out)


def _escape_encode(data):
    """PostgreSQL's escape_encode(): backslash is doubled, bytes outside
    0x20..0x7e become \\ooo (three octal digits)."""
    out = []
    for b in data:
        if b == 0x5C:
            out.append("\\\\")
        elif b < 0x20 or b > 0x7E:
            out.append("\\%03o" % b)
        else:
            out.append(chr(b))
    return "".join(out)


def encode(data, fmt):
    if (not isinstance(data, (bytes, bytearray)) or not isinstance(fmt, str)
            or isinstance(fmt, Bits)):
        _no_func("encode", data, fmt)
    f = fmt.lower()
    if f == "hex":
        return bytes(data).hex()
    if f == "base64":
        return _b64_encode(bytes(data))
    if f == "escape":
        return _escape_encode(bytes(data))
    raise BinError('unrecognized encoding: "%s"' % fmt)


def decode(text, fmt):
    if (not isinstance(text, str) or isinstance(text, Bits)
            or not isinstance(fmt, str) or isinstance(fmt, Bits)):
        _no_func("decode", text, fmt)
    f = fmt.lower()
    if f == "hex":
        return hex_decode(text)
    if f == "base64":
        return _b64_decode(text)
    if f == "escape":
        return escape_decode(text)
    raise BinError('unrecognized encoding: "%s"' % fmt)


def get_byte(data, n):
    if not isinstance(data, (bytes, bytearray)) or not _is_int(n):
        _no_func("get_byte", data, n)
    if n < 0 or n >= len(data):
        raise BinError("index %d out of valid range, 0..%d"
                       % (n, len(data) - 1))
    return data[n]


def set_byte(data, n, value):
    if (not isinstance(data, (bytes, bytearray)) or not _is_int(n)
            or not _is_int(value)):
        _no_func("set_byte", data, n, value)
    if n < 0 or n >= len(data):
        raise BinError("index %d out of valid range, 0..%d"
                       % (n, len(data) - 1))
    b = bytearray(data)
    b[n] = value & 0xFF
    return bytes(b)


def get_bit(data, n):
    if not _is_int(n) or not isinstance(data, (Bits, bytes, bytearray)):
        _no_func("get_bit", data, n)
    if isinstance(data, Bits):
        if n < 0 or n >= len(data):
            raise BinError("bit index %d out of valid range (0..%d)"
                           % (n, len(data) - 1))
        return int(data[n])
    if n < 0 or n >= len(data) * 8:
        raise BinError("index %d out of valid range, 0..%d"
                       % (n, len(data) * 8 - 1))
    return (data[n // 8] >> (7 - n % 8)) & 1


def set_bit(data, n, value):
    if (not _is_int(n) or not _is_int(value)
            or not isinstance(data, (Bits, bytes, bytearray))):
        _no_func("set_bit", data, n, value)
    if isinstance(data, Bits):
        if n < 0 or n >= len(data):
            raise BinError("bit index %d out of valid range (0..%d)"
                           % (n, len(data) - 1))
        if value not in (0, 1):
            raise BinError("new bit must be 0 or 1")
        return Bits(data[:n] + str(value) + data[n + 1:])
    if n < 0 or n >= len(data) * 8:
        raise BinError("index %d out of valid range, 0..%d"
                       % (n, len(data) * 8 - 1))
    if value not in (0, 1):
        raise BinError("new bit must be 0 or 1")
    b = bytearray(data)
    mask = 1 << (7 - n % 8)
    if value:
        b[n // 8] |= mask
    else:
        b[n // 8] &= ~mask
    return bytes(b)


def bit_count(data):
    if isinstance(data, Bits):
        return data.count("1")
    if isinstance(data, (bytes, bytearray)):
        return sum(bin(b).count("1") for b in data)
    _no_func("bit_count", data)


def length(data):
    if not isinstance(data, (Bits, bytes, bytearray, str)):
        _no_func("length", data)
    return len(data)


def bit_length(data):
    if isinstance(data, Bits):
        return len(data)
    if isinstance(data, (bytes, bytearray)):
        return len(data) * 8
    if isinstance(data, str):
        return len(data.encode("utf-8")) * 8
    _no_func("bit_length", data)


def substr(data, start, count=None):
    args = (data, start) if count is None else (data, start, count)
    if (not isinstance(data, (Bits, bytes, bytearray, str))
            or not _is_int(start)
            or (count is not None and not _is_int(count))):
        _no_func("substr", *args)
    if count is not None and count < 0:
        raise BinError("negative substring length not allowed")
    s1 = max(start, 1)
    if count is None:
        result = data[s1 - 1:]
    else:
        l1 = max(0, start + count - s1)
        result = data[s1 - 1:s1 - 1 + l1]
    if isinstance(data, Bits):
        return Bits(result)
    return bytes(result) if isinstance(result, bytearray) else result


def overlay(data, new, start, count=None):
    args = (data, new, start) if count is None else (data, new, start, count)
    if isinstance(data, Bits) or isinstance(new, Bits):
        _no_func("overlay", *args)
    if not _is_int(start) or (count is not None and not _is_int(count)):
        _no_func("overlay", *args)
    if isinstance(data, (bytes, bytearray)):
        if not isinstance(new, (bytes, bytearray)):
            _no_func("overlay", *args)
        data, new = bytes(data), bytes(new)
    elif isinstance(data, str):
        if not isinstance(new, str):
            _no_func("overlay", *args)
    else:
        _no_func("overlay", *args)
    if count is None:
        count = len(new)
    elif count < 0:
        raise BinError("negative substring length not allowed")
    sp1 = min(max(start, 1), len(data) + 1)
    return data[:sp1 - 1] + new + data[sp1 - 1 + count:]


def btrim(data, chars=None):
    args = (data,) if chars is None else (data, chars)
    if isinstance(data, Bits) or isinstance(chars, Bits):
        _no_func("btrim", *args)
    if isinstance(data, (bytes, bytearray)):
        if chars is None:
            chars = b" "
        if not isinstance(chars, (bytes, bytearray)):
            _no_func("btrim", *args)
        return bytes(data).strip(bytes(chars))
    if isinstance(data, str):
        if chars is None:
            chars = " "
        if not isinstance(chars, str):
            _no_func("btrim", *args)
        return data.strip(chars)
    _no_func("btrim", *args)


def concat(a, b):
    if isinstance(a, Bits) and isinstance(b, Bits):
        return Bits(a + b)
    if isinstance(a, (bytes, bytearray)) and isinstance(b, (bytes, bytearray)):
        return bytes(a) + bytes(b)
    if (isinstance(a, str) and isinstance(b, str)
            and not isinstance(a, Bits) and not isinstance(b, Bits)):
        return a + b
    _no_oper("||", a, b)


def bit_cast(bits, n):
    """bit string -> bit(n): an explicit cast, so overlong input is
    truncated on the right and short input is zero-padded on the right."""
    if not isinstance(bits, Bits) or not _is_int(n):
        _no_func("bit", bits, n)
    if n <= 0 or len(bits) == n:
        return bits
    if len(bits) > n:
        return Bits(bits[:n])
    return Bits(bits + "0" * (n - len(bits)))


def _int(bits):
    return int(bits, 2) if bits else 0


def _bits(value, width):
    return Bits(format(value, "b").zfill(width)) if width > 0 else Bits("")


def _bit_pair(a, b, op):
    if not isinstance(a, Bits) or not isinstance(b, Bits):
        _no_oper(op, a, b)
    if len(a) != len(b):
        raise BinError("bit strings must be of equal length")


def bit_and(a, b):
    _bit_pair(a, b, "&")
    return _bits(_int(a) & _int(b), len(a))


def bit_or(a, b):
    _bit_pair(a, b, "|")
    return _bits(_int(a) | _int(b), len(a))


def bit_xor(a, b):
    _bit_pair(a, b, "#")
    return _bits(_int(a) ^ _int(b), len(a))


def bit_not(a):
    if not isinstance(a, Bits):
        _no_oper("~", a)
    return Bits("".join("1" if c == "0" else "0" for c in a))


def shift_left(a, n):
    if not isinstance(a, Bits) or not _is_int(n):
        _no_oper("<<", a, n)
    if n < 0:
        return shift_right(a, -n)
    width = len(a)
    if width == 0:
        return a
    return _bits((_int(a) << n) & ((1 << width) - 1), width)


def shift_right(a, n):
    if not isinstance(a, Bits) or not _is_int(n):
        _no_oper(">>", a, n)
    if n < 0:
        return shift_left(a, -n)
    return _bits(_int(a) >> n, len(a))


FUNCTIONS = {
    "bytea": bytea, "encode": encode, "decode": decode, "get_byte": get_byte, "set_byte": set_byte,
    "get_bit": get_bit, "set_bit": set_bit, "bit_count": bit_count, "length": length,
    "bit_length": bit_length, "substr": substr, "overlay": overlay, "btrim": btrim, "||": concat,
    "bit": bit_cast, "&": bit_and, "|": bit_or, "#": bit_xor, "~": bit_not, "<<": shift_left, ">>": shift_right,
}
