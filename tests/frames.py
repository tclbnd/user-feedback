"""Builders for small synthetic DPX files used by the tests."""
import math
import struct


def bcd(v):
    return (v // 10) << 4 | v % 10


def _line_bytes(width, comps, depth, packing):
    """Line size per SMPTE ST 268 packing method (mirrors dpxkit.dpx.line_bytes)."""
    if packing == 0:
        return math.ceil(width * comps * depth / 32) * 4
    if packing == 1:  # filled method A: components packed into 32-bit words
        return math.ceil(width * comps / max(32 // depth, 1)) * 4
    # filled method B: word-level padding, 4 samples/word at 10-bit, 2 at 12-bit
    words = 4 if depth == 10 else (2 if depth == 12 else 1)
    return 4 * width * math.ceil(comps / words)


def dpx(width=2048, height=1556, elements=((50, 10, 1, 0),), film_rate=24.0, timecode=(1, 0, 0, 0),
        stamp="2024:03:15:10:22:05:UTC", version="V2.0", creator="Scanner", endian="big",
        film_len=1664, tv_len=384, user=0, tv_rate=25.0):
    e = "<" if endian == "little" else ">"
    interlace, encrypted, data, cut, eol = 0xFF, False, True, 0, 0
    """elements: (descriptor, bit depth, packing, encoding)."""
    h = bytearray(b"\xff" * 2048)
    magic = b"SDPX" if e == ">" else b"XPDS"
    h[0:4] = magic
    p = lambda fmt, off, *v: struct.pack_into(e + fmt, h, off, *v)
    image_offset = 2048 + user
    p("I", 4, image_offset)
    h[8:16] = version.encode().ljust(8, b"\0")
    p("I", 20, 0)
    p("III", 24, film_len, tv_len, user)
    h[36:136] = bytes(100)
    h[136:160] = (stamp or "").encode().ljust(24, b"\0")
    h[160:260] = creator.encode().ljust(100, b"\0")
    h[260:660] = bytes(400)
    p("I", 660, 0 if encrypted else 0xFFFFFFFF)
    p("HHII", 768, 0, len(elements), width, height)
    sizes, off = [], image_offset
    for i, (desc, depth, packing, enc) in enumerate(elements):
        b = 780 + 72 * i
        h[b:b + 72] = bytes(72)
        comps = {50: 3, 51: 4, 100: 2, 6: 1, 102: 4, 103: 4, 101: 3}.get(desc, 1)
        lb = _line_bytes(width, comps, depth, packing)
        size = (lb + eol) * height
        h[b + 20], h[b + 21], h[b + 22], h[b + 23] = desc, 2, 2, depth
        p("HHII", b + 24, packing, enc, off, eol if eol else 0)
        sizes.append(size)
        off += size
    h[1664:1724] = bytes(60)
    if film_len:
        if film_rate is not None:
            p("f", 1724, film_rate)
    if tv_len:
        if timecode is not None:
            hh, mm, ss, ff = timecode
            p("I", 1920, bcd(hh) << 24 | bcd(mm) << 16 | bcd(ss) << 8 | bcd(ff))
        h[1928] = interlace
        if tv_rate is not None:
            p("f", 1940, tv_rate)
    out = bytes(h) + bytes(user) + (bytes(sum(sizes)) if data else b"")
    out = out[:len(out) - cut] if cut else out
    out = bytearray(out)
    struct.pack_into(e + "I", out, 16, len(out))
    return bytes(out)
