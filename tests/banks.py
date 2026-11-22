"""Builders for small synthetic SoundBanks used by the tests."""
import struct

# Test banks are little-endian by default; flip to ">" for console banks.
E = "<"


def set_endian(prefix):
    global E
    E = prefix


def chunk(tag, body):
    return tag.encode() + struct.pack(E + "I", len(body)) + body


def bkhd(version=134, bank_id=0x1234ABCD):
    return chunk("BKHD", struct.pack(E + "II", version, bank_id) + bytes(12))


def media(items, align=16):
    """items: list of (id, size). Returns (DIDX chunk, DATA chunk)."""
    didx, data = b"", b""
    for mid, size in items:
        data += bytes(-len(data) % align)
        didx += struct.pack(E + "III", mid, len(data), size)
        data += bytes(size)
    return chunk("DIDX", didx), chunk("DATA", data)


def obj(otype, oid, body):
    # Wwise object size covers the object id plus the body that follows it.
    return struct.pack(E + "BII", otype, len(body) + 4, oid) + body


def sound(oid, source, stream=0, version=134):
    if version <= 88:
        body = struct.pack(E + "III", 0x00040001, stream, source)
    else:
        body = struct.pack(E + "IBI", 0x00040001, stream, source)
    return obj(2, oid, body + struct.pack(E + "I", 1000) + bytes(10))


def music_track(oid, source, stream=0, version=134):
    # Music tracks (HIRC type 11) carry the same source header as sounds.
    if version <= 88:
        body = struct.pack(E + "III", 0x00040001, stream, source)
    else:
        body = struct.pack(E + "IBI", 0x00040001, stream, source)
    return obj(11, oid, body + bytes(20))


def event(oid, actions, version=134):
    if version >= 122:
        body = bytes([len(actions)]) + b"".join(struct.pack(E + "I", a) for a in actions)
    else:
        body = struct.pack(E + "I", len(actions)) + b"".join(struct.pack(E + "I", a) for a in actions)
    return obj(4, oid, body)


def action(oid, target):
    return obj(3, oid, struct.pack(E + "HI", 0x0403, target) + bytes(6))


def hirc(*objs):
    return chunk("HIRC", struct.pack(E + "I", len(objs)) + b"".join(objs))


def stid(names):
    body = struct.pack(E + "II", 1, len(names))
    for sid, name in names:
        n = name.encode()
        body += struct.pack(E + "IB", sid, len(n)) + n
    return chunk("STID", body)


def bank(version=134, bank_id=0x1234ABCD, wems=((101, 5000), (102, 3000)), objs=None, names=None, extra=b""):
    objs = objs if objs is not None else [sound(11, 101, 0, version), sound(12, 102, 0, version), sound(13, 999, 2, version),
                                         action(21, 11), event(31, [21], version)]
    out = bkhd(version, bank_id)
    if wems:
        d1, d2 = media(wems)
        out += d1 + d2
    out += extra + hirc(*objs)
    if names:
        out += stid(names)
    return out
