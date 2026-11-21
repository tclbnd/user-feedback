"""Matroska / WebM: tracks and cluster blocks."""
import struct
from fractions import Fraction

from .ebml import EbmlError, element, uint, vint

SEGMENT, INFO, TRACKS, CLUSTER = 0x18538067, 0x1549A966, 0x1654AE6B, 0x1F43B675
TRACK_TYPES = {1: "video", 2: "audio", 3: "complex", 0x10: "logo", 0x11: "subtitle", 0x12: "buttons", 0x20: "control"}


def children(data, start, end):
    pos = start
    while pos < end:
        eid, body, size = element(data, pos)
        if size is None:
            raise EbmlError("unknown size not allowed here")
        yield eid, body, body + size
        pos = body + size


def read_info(data, s, e):
    scale, duration = 1000000, None
    for eid, b, f in children(data, s, e):
        if eid == 0x2AD7B1:
            scale = uint(data[b:f])
        elif eid == 0x4489:
            duration = struct.unpack(">f", data[b:f])[0]
    return scale, duration


def read_tracks(data, s, e):
    out = {}
    for eid, b, f in children(data, s, e):
        if eid != 0xAE:
            continue
        t = {"number": None, "type": None, "codec": None, "enabled": False, "default": False, "language": None}
        for cid, cb, cf in children(data, b, f):
            if cid == 0xD7:
                t["number"] = uint(data[cb:cf])
            elif cid == 0x83:
                t["type"] = TRACK_TYPES.get(uint(data[cb:cf]), "unknown")
            elif cid == 0x86:
                t["codec"] = data[cb:cf].decode("ascii")
            elif cid == 0xB9:
                t["enabled"] = bool(uint(data[cb:cf]))
            elif cid == 0x88:
                t["default"] = bool(uint(data[cb:cf]))
            elif cid == 0x22B59C:
                t["language"] = data[cb:cf].decode("ascii")
        out[t["number"]] = t
    return out


def lace_sizes(data, p, end, flags):
    """Return list of frame sizes for the block payload starting at p (after flags)."""
    lacing = (flags >> 1) & 3
    if lacing == 0:
        return [end - p], p
    count = data[p] + 1
    p += 1
    if lacing == 1:
        sizes = []
        for _ in range(count - 1):
            sizes.append(data[p])
            p += 1
        sizes.append(end - p - sum(sizes))
    else:
        first, n, _ = vint(data, p)
        p += n
        sizes = [first]
        for _ in range(count - 2):
            raw, n, _ = vint(data, p)
            p += n
            sizes.append(sizes[-1] + raw)
        sizes.append(end - p - sum(sizes))
    if any(s < 0 for s in sizes):
        raise EbmlError("negative lace size")
    return sizes, p


def block(data, b, f):
    track, n = data[b] & 0x7F, 1
    (rel,) = struct.unpack_from(">H", data, b + n)
    flags = data[b + n + 2]
    sizes, _ = lace_sizes(data, b + n + 3, f, flags)
    return track, rel, flags, sizes


def scan(data):
    if element(data, 0)[0] != 0x1A45DFA3:
        raise EbmlError("not an EBML file")
    eid, b, size = element(data, 0)
    pos = b + size
    eid, seg, size = element(data, pos)
    if eid != SEGMENT:
        raise EbmlError("no Segment")
    seg_end = min(len(data), seg + size)
    scale, duration, tracks = 1000000, None, {}
    stats = {}
    clusters = 0
    pos = seg
    while pos < seg_end:
        eid, body, size = element(data, pos)
        if eid == INFO:
            scale, duration = read_info(data, body, body + size)
        elif eid == TRACKS:
            tracks = read_tracks(data, body, body + size)
        if eid != CLUSTER:
            pos = body + size
            continue
        clusters += 1
        end = body + size
        cts = 0
        p = body
        while p < end:
            cid, cb, csize = element(data, p)
            cf = cb + csize
            if cid == 0xE7:
                cts = uint(data[cb:cf])
            elif cid == 0xA3:
                tr, rel, flags, sizes = block(data, cb, cf)
                record(stats, tr, cts + rel, sizes, bool(flags & 0x80))
            elif cid == 0xA0:
                for gid, gb, gf in children(data, cb, cf):
                    if gid == 0xA1:
                        tr, rel, flags, sizes = block(data, gb, gf)
                        record(stats, tr, cts + rel, sizes, bool(flags & 0x80))
            p = cf
        pos = p
    out = []
    for num in sorted(tracks):
        t = tracks[num]
        s = stats.get(num, {"blocks": 0, "frames": 0, "keyframes": 0, "bytes": 0, "first": None, "last": None})
        ms = lambda v: None if v is None else str(Fraction(v))
        out.append({**t, "blocks": s["blocks"], "frames": s["frames"], "keyframes": s["keyframes"], "bytes": s["bytes"],
                    "first_ms": ms(s["first"]), "last_ms": ms(s["last"])})
    return {"timestamp_scale": scale, "duration_ms": None if duration is None else round(duration * scale / 1000000, 3),
            "clusters": clusters, "tracks": out}


def record(stats, track, ts, sizes, key):
    s = stats.setdefault(track, {"blocks": 0, "frames": 0, "keyframes": 0, "bytes": 0, "first": None, "last": None})
    s["blocks"] += 1
    s["frames"] += len(sizes)
    s["keyframes"] += 1 if key else 0
    s["bytes"] += sum(sizes)
    s["first"] = ts if s["first"] is None else min(s["first"], ts)
    s["last"] = ts if s["last"] is None else max(s["last"], ts)
