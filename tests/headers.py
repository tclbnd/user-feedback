"""Header builders for the tests."""
import struct

MAGIC = b"\x76\x2f\x31\x01"


def attr(name, typ, value):
    return name.encode() + b"\0" + typ.encode() + b"\0" + struct.pack("<i", len(value)) + value


def chlist(*chans):
    out = b""
    for name, ptype in chans:
        out += name.encode() + b"\0" + struct.pack("<iB3xii", ptype, 0, 1, 1)
    return out + b"\0"


def box2i(x0, y0, x1, y1):
    return struct.pack("<4i", x0, y0, x1, y1)


def tiledesc(tx, ty, level_mode, rounding):
    return struct.pack("<IIB", tx, ty, level_mode | rounding << 4)


def file(version, *parts):
    out = MAGIC + struct.pack("<I", version)
    for attrs in parts:
        out += attrs + b"\0"
    if len(parts) > 1:
        out += b"\0"
    return out


def minimal():
    a = attr("compression", "compression", b"\x01")
    a += attr("lineOrder", "lineOrder", b"\x00")
    return file(2, a)
