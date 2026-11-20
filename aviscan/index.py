"""Streams and per-stream chunk index of an AVI file."""
from fractions import Fraction

from .riff import AviError, find, walk


def _u32(data, pos):
    return int.from_bytes(data[pos:pos + 4], "little")


def streams(data, hdrl):
    out = []
    avih = find(hdrl.children, "avih")
    usec = _u32(data, avih.offset + 8)
    for strl in [c for c in hdrl.children if c.list_type == "strl"]:
        strh = find(strl.children, "strh")
        base = strh.offset + 8
        kind = data[base:base + 4].decode("latin-1")
        handler = data[base + 4:base + 8].decode("latin-1")
        out.append({"type": kind, "handler": handler,
                    "rate": str(Fraction(1000000, usec)) if kind == "vids" else str(Fraction(_u32(data, base + 24), _u32(data, base + 20))),
                    "length": _u32(data, base + 32)})
    return out


def entries(data, top):
    idx1 = find(top.children, "idx1")
    out = {}
    if idx1 is None:
        return out
    pos = idx1.offset + 8
    for i in range(idx1.size // 16):
        e = pos + 16 * i
        ckid = data[e:e + 4].decode("latin-1")
        flags, offset, size = _u32(data, e + 4), _u32(data, e + 8), _u32(data, e + 12)
        out.setdefault(int(ckid[:2]), []).append({"offset": offset + 8, "size": size, "key": bool(flags & 1)})
    return out


def scan(data):
    if data[:4] != b"RIFF" or data[8:12] != b"AVI ":
        raise AviError("not an AVI file")
    top = walk(data, 0, len(data))[0]
    hdrl = find(top.children, "LIST", "hdrl")
    info = streams(data, hdrl)
    idx = entries(data, top)
    for n, s in enumerate(info):
        es = idx.get(n, [])
        s["chunks"] = len(es)
        s["keyframes"] = sum(1 for e in es if e["key"])
        s["first"] = es[:3]
        if s["type"] == "vids":
            s["duration"] = str(len(es) / Fraction(s["rate"]))
    return info
