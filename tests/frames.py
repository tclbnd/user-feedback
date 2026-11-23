"""Builders for small synthetic DPX files used by the tests."""
import math
import struct


def bcd(v):
    return (v // 10) << 4 | v % 10


def dpx(width=2048, height=1556, elements=((50, 10, 1, 0),), film_rate=24.0, tv_rate=None,
        timecode=(1, 0, 0, 0), stamp="2024:03:15:10:22:05:UTC", version="V2.0", creator="Scanner",
        orientation=0, interlace=0xFF, tv_fields=None, film_fields=None, pixel_aspect=None,
        encrypted=False, endian="big", data=True, cut=0, user=0, eol=0):
    """Build a DPX frame.

    elements: (descriptor, bit depth, packing, encoding) per image element.
    film_rate and tv_rate are left undefined when None; a header field may also
    be overridden with a raw dict of {offset: bytes}.
    """
    e = ">" if endian == "big" else "<"
    h = bytearray(b"\xff" * 2048)
    h[0:4] = b"SDPX" if e == ">" else b"XPDS"
    p = lambda fmt, off, *v: struct.pack_into(e + fmt, h, off, *v)
    image_offset = 2048 + user
    p("I", 4, image_offset)
    h[8:16] = version.encode().ljust(8, b"\0")
    p("I", 20, 0)
    p("III", 24, 1664, 384, user)
    h[36:136] = bytes(100)
    h[136:160] = (stamp or "").encode().ljust(24, b"\0")
    h[160:260] = creator.encode().ljust(100, b"\0")
    h[260:660] = bytes(400)
    p("I", 660, 0x01234567 if encrypted else 0xFFFFFFFF)
    p("HHII", 768, orientation, len(elements), width, height)
    if pixel_aspect is not None:
        p("II", 1628, *pixel_aspect)
    sizes, off = [], image_offset
    for i, (desc, depth, packing, enc) in enumerate(elements):
        b = 780 + 72 * i
        h[b:b + 72] = bytes(72)
        comps = {50: 3, 51: 4, 52: 4, 100: 2, 101: 3, 102: 3, 103: 4, 6: 1}.get(desc, 1)
        if packing in (1, 2):
            lb = math.ceil(width * comps / (32 // depth)) * 4
        else:
            lb = math.ceil(width * comps * depth / 32) * 4
        size = (lb + eol) * height
        h[b + 20], h[b + 21], h[b + 22], h[b + 23] = desc, 2, 2, depth
        p("HHIII", b + 24, packing, enc, off, eol, 0)
        sizes.append(size)
        off += size
    h[1664:1724] = bytes(60)
    if film_rate is not None:
        p("f", 1724, film_rate)
    if timecode is not None:
        hh, mm, ss, ff = timecode
        p("I", 1920, bcd(hh) << 24 | bcd(mm) << 16 | bcd(ss) << 8 | bcd(ff))
    h[1928] = interlace
    if tv_rate is not None:
        p("f", 1940, tv_rate)
    for fields in (film_fields, tv_fields):
        for off, value in (fields or {}).items():
            h[off:off + len(value)] = value
    out = bytes(h) + bytes(user) + (bytes(sum(sizes)) if data else b"")
    out = out[:len(out) - cut] if cut else out
    out = bytearray(out)
    struct.pack_into(e + "I", out, 16, len(out))
    return bytes(out)
