"""Builders for OpenEXR headers used by the tests."""
import struct


def attr(name, typ, value):
    return (name.encode("latin-1") + b"\0" + typ.encode("latin-1") + b"\0" +
            struct.pack("<i", len(value)) + value)


def minimal():
    a = attr("compression", "compression", b"\x01")
    a += attr("lineOrder", "lineOrder", b"\x00")
    return b"\x76\x2f\x31\x01" + struct.pack("<I", 2) + a + b"\0"


def box2i(x0, y0, x1, y1):
    return struct.pack("<4i", x0, y0, x1, y1)


def chlist(entries):
    """entries: (name, pixel_type, xsampling, ysampling)."""
    out = b""
    for name, ptype, xs, ys in entries:
        out += name.encode("latin-1") + b"\0" + struct.pack("<iB3xii", ptype, 0, xs, ys)
    return out + b"\0"


def tiledesc(x, y, mode, rounding):
    return struct.pack("<IIB", x, y, mode | (rounding << 4))


def rational(num, den):
    return struct.pack("<II", num, den)


def header(*attrs, version=2, trailing=b""):
    return (b"\x76\x2f\x31\x01" + struct.pack("<I", version) +
            b"".join(attrs) + b"\0" + trailing)


def multipart(*headers, version=0x1002):
    """One file whose version flag marks it multi-part; empty header ends the list."""
    return (b"\x76\x2f\x31\x01" + struct.pack("<I", version) +
            b"".join(headers) + b"\0")


def part(name, typ, *attrs, compression=b"\x00", window=(0, 0, 9, 9)):
    head = attr("name", "string", name.encode("latin-1") + b"\0")
    head += attr("type", "string", typ.encode("latin-1") + b"\0")
    head += attr("compression", "compression", compression)
    head += attr("dataWindow", "box2i", box2i(*window))
    head += attr("displayWindow", "box2i", box2i(*window))
    return head + b"".join(attrs) + b"\0"
