"""Header builders for the tests: attributes and whole files with just a header."""
import struct

MAGIC = b"\x76\x2f\x31\x01"


def attr(name, typ, value):
    return name.encode("latin-1") + b"\0" + typ.encode("latin-1") + b"\0" + struct.pack("<i", len(value)) + value


def chlist(*chs):
    out = b""
    for name, ptype, xs, ys in chs:
        out += name.encode("latin-1") + b"\0" + struct.pack("<iB3xii", ptype, 0, xs, ys)
    return out + b"\0"


def box2i(x0, y0, x1, y1):
    return struct.pack("<4i", x0, y0, x1, y1)


def tiledesc(x, y, level_mode, rounding):
    return struct.pack("<IIB", x, y, (rounding << 4) | level_mode)


def rational(num, den):
    return struct.pack("<ii", num, den)


def string(text):
    return text.encode("latin-1") + b"\0"


def part(*attributes):
    return b"".join(attributes) + b"\0"


def file(version, *parts):
    # A trailing empty header: what ends a multipart file's list of headers.
    return MAGIC + struct.pack("<I", version) + b"".join(parts) + b"\0"


def minimal():
    a = attr("compression", "compression", b"\x01")
    a += attr("lineOrder", "lineOrder", b"\x00")
    return file(2, part(a))


def scanline(compression=1, window=(0, 0, 99, 49), extra=()):
    a = attr("channels", "chlist", chlist(("R", 1, 1, 1), ("Z", 2, 1, 1)))
    a += attr("compression", "compression", struct.pack("<B", compression))
    a += attr("dataWindow", "box2i", box2i(*window))
    a += attr("displayWindow", "box2i", box2i(*window))
    a += attr("lineOrder", "lineOrder", b"\x00")
    a += attr("pixelAspectRatio", "float", struct.pack("<f", 1.0))
    for name, typ, value in extra:
        a += attr(name, typ, value)
    return part(a)


def tiled(name, typ, window, tiles, compression=8, extra=()):
    a = attr("name", "string", string(name))
    a += attr("type", "string", string(typ))
    a += attr("channels", "chlist", chlist(("R", 1, 1, 1)))
    a += attr("compression", "compression", struct.pack("<B", compression))
    a += attr("dataWindow", "box2i", box2i(*window))
    a += attr("tiles", "tiledesc", tiles)
    for name_, typ_, value in extra:
        a += attr(name_, typ_, value)
    return part(a)
