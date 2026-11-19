"""Literals of the blob engine (PostgreSQL bytea and bit string semantics).

A string argument that starts with \\x is a bytea literal in hex form,
B'0101' is a bit string literal, anything else is text.  Results are
rendered the way psql prints them (bytea in hex output format).
"""


class BinError(Exception):
    pass


class Bits(str):
    """A bit string such as '10110'."""


def parse(arg):
    if isinstance(arg, str) and arg.startswith("\\x"):
        return bytes.fromhex(arg[2:])
    if isinstance(arg, str) and arg.startswith("B'") and arg.endswith("'"):
        return Bits(arg[2:-1])
    return arg


def show(v):
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        return "\\x" + bytes(v).hex()
    return str(v)
