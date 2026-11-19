"""Literals of the blob engine (PostgreSQL bytea and bit string semantics).

A string argument that starts with \\x is a bytea literal in hex form,
B'0101' is a bit string literal, anything else is text.  Results are
rendered the way psql prints them (bytea in hex output format).
"""


class BinError(Exception):
    pass


class Bits(str):
    """A bit string such as '10110'."""


def type_name(v):
    """The PostgreSQL type name reported for a value in error messages."""
    if v is None:
        return "unknown"
    if isinstance(v, Bits):
        return "bit"
    if isinstance(v, (bytes, bytearray)):
        return "bytea"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, str):
        return "text"
    return "unknown"


_HEX_VALUES = {c: i for i, c in enumerate("0123456789abcdef")}
_HEX_VALUES.update({c: i for i, c in enumerate("0123456789ABCDEF")})


def hex_decode(text):
    """PostgreSQL's hex_decode(): whitespace between byte pairs is
    ignored, everything else must be pairs of hex digits."""
    out = bytearray()
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \n\t\r":
            i += 1
            continue
        v1 = _HEX_VALUES.get(c)
        if v1 is None:
            raise BinError('invalid hexadecimal digit: "%s"' % c)
        i += 1
        if i >= n:
            raise BinError("invalid hexadecimal data: odd number of digits")
        v2 = _HEX_VALUES.get(text[i])
        if v2 is None:
            raise BinError('invalid hexadecimal digit: "%s"' % text[i])
        i += 1
        out.append((v1 << 4) | v2)
    return bytes(out)


def escape_decode(text):
    """PostgreSQL's escape_decode(): a backslash starts either a \\\\
    (one literal backslash) or an \\ooo sequence (one to three octal
    digits, the first at most 3); any other backslash use is an error."""
    out = bytearray()
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        i += 1
        if c != "\\":
            out.extend(c.encode("utf-8"))
            continue
        if i >= n:
            raise BinError("invalid input syntax for type bytea")
        c = text[i]
        i += 1
        if c == "\\":
            out.append(0x5C)
            continue
        if "0" <= c <= "3":
            v = ord(c) - ord("0")
            for _ in range(2):
                if i < n and "0" <= text[i] <= "7":
                    v = (v << 3) + ord(text[i]) - ord("0")
                    i += 1
                else:
                    break
            out.append(v)
            continue
        raise BinError("invalid input syntax for type bytea")
    return bytes(out)


def bytea_in(text):
    """text -> bytea (the type input function, escape or hex form)"""
    if text.startswith("\\x"):
        return hex_decode(text[2:])
    return escape_decode(text)


def parse(arg):
    if isinstance(arg, str) and arg.startswith("\\x"):
        return hex_decode(arg[2:])
    if isinstance(arg, str) and arg.startswith("B'") and arg.endswith("'"):
        bits = arg[2:-1]
        for c in bits:
            if c not in "01":
                raise BinError('"%s" is not a valid binary digit' % c)
        return Bits(bits)
    return arg


def show(v):
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        return "\\x" + bytes(v).hex()
    return str(v)
