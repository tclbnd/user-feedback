"""Small PGS streams for the tests."""
import struct


def seg(kind, body, pts=0):
    return b"PG" + struct.pack(">IIBH", pts, 0, kind, len(body)) + body


def subtitle(pts, w=100, h=2):
    pcs = struct.pack(">HHBHBBBB", 1920, 1080, 0x10, 0, 0x80, 0, 0, 1) + struct.pack(">HBBHH", 0, 0, 0, 10, 20)
    pds = bytes([0, 0]) + bytes([1, 235, 128, 128, 255])
    picture = (bytes([0, 0x80 | 50, 1]) + bytes([0, 50]) + b"\x00\x00") * h
    ods = struct.pack(">HBB", 0, 0, 0xC0) + (4 + len(picture)).to_bytes(3, "big") + struct.pack(">HH", w, h) + picture
    return seg(0x16, pcs, pts) + seg(0x14, pds, pts) + seg(0x15, ods, pts) + seg(0x80, b"", pts)


def pcs(comps, pts=0, state=0x80, palette_update=0):
    """A presentation composition segment; comps are (object id, flags, x, y, crop or None)."""
    body = struct.pack(">HHBHBBBB", 1920, 1080, 0x10, 0, state, palette_update, 0, len(comps))
    for oid, flags, x, y, crop in comps:
        body += struct.pack(">HBBHH", oid, 0, flags, x, y)
        if crop is not None:
            body += struct.pack(">HHHH", *crop)
    return seg(0x16, body, pts)


def end(pts=0):
    return seg(0x80, b"", pts)


def clear(pts):
    """An empty display set: takes the current subtitle off screen."""
    return pcs([], pts, state=0) + end(pts)


def fragmented_ods(oid, w, h, picture, split, pts=0):
    """An object definition split into a first and a last fragment."""
    first = struct.pack(">HBB", oid, 0, 0x40) + (4 + len(picture)).to_bytes(3, "big") \
        + struct.pack(">HH", w, h) + picture[:split]
    last = struct.pack(">HBB", oid, 0, 0x80) + picture[split:]
    return seg(0x15, first, pts) + seg(0x15, last, pts)
