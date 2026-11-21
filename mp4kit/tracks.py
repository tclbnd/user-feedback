"""Track sample tables, edit lists and timing."""
import struct
from fractions import Fraction

from .boxes import Mp4Error, boxes, find, full, path


def _time_header(data, s):
    """mvhd / mdhd: (timescale, duration). Version 1 uses 64-bit fields."""
    version, _, p = full(data, s)
    if version == 1:
        (scale,) = struct.unpack_from(">I", data, p + 16)
        (dur,) = struct.unpack_from(">Q", data, p + 20)
        return scale, dur
    return struct.unpack_from(">II", data, p + 8)


def _entries(data, s, fmt):
    _, _, p = full(data, s)
    (n,) = struct.unpack_from(">I", data, p)
    size = struct.calcsize(fmt)
    return [struct.unpack_from(fmt, data, p + 4 + i * size) for i in range(n)]


def sample_sizes(data, s):
    _, _, p = full(data, s)
    size, n = struct.unpack_from(">II", data, p)
    if size:
        return [size] * n
    return list(struct.unpack_from(">%dI" % n, data, p + 8))


def chunk_offsets(data, stbl):
    hit = find(data, *stbl, "co64")
    if hit:
        return [o for (o,) in _entries(data, hit[0], ">Q")]
    hit = find(data, *stbl, "stco")
    if hit:
        return [o for (o,) in _entries(data, hit[0], ">I")]
    raise Mp4Error("no chunk offset box")


def sample_offsets(sizes, offsets, stsc):
    out = []
    i = 0
    for k, (first, per_chunk, _) in enumerate(stsc):
        last = stsc[k + 1][0] - 1 if k + 1 < len(stsc) else len(offsets)
        for chunk in range(first, last + 1):
            pos = offsets[chunk - 1]
            for _ in range(per_chunk):
                if i >= len(sizes):
                    return out
                out.append(pos)
                pos += sizes[i]
                i += 1
    return out


def edits(data, edts):
    hit = find(data, *edts, "elst")
    if hit is None:
        return None
    _, _, p = full(data, hit[0])
    version = data[hit[0]]
    (n,) = struct.unpack_from(">I", data, p)
    p += 4
    out = []
    for _ in range(n):
        if version == 1:
            dur, mt = struct.unpack_from(">Qq", data, p)
            p += 20
        else:
            dur, mt = struct.unpack_from(">Ii", data, p)
            p += 12
        out.append((dur, mt))
    return out


def track(data, s, e, movie_scale):
    tkhd = find(data, s, e, "tkhd")
    version, flags, p = full(data, tkhd[0])
    tid = struct.unpack_from(">I", data, p + (16 if version == 1 else 8))[0]
    mdia = find(data, s, e, "mdia")
    scale, mdur = _time_header(data, find(data, *mdia, "mdhd")[0])
    hdlr = find(data, *mdia, "hdlr")
    handler = data[hdlr[0] + 8:hdlr[0] + 12].decode("latin-1")
    stbl = path(data, *mdia, "minf", "stbl")
    stts = _entries(data, find(data, *stbl, "stts")[0], ">II")
    sizes = sample_sizes(data, find(data, *stbl, "stsz")[0])
    stsc = _entries(data, find(data, *stbl, "stsc")[0], ">III")
    offsets = chunk_offsets(data, stbl)
    where = sample_offsets(sizes, offsets, stsc)
    stss = find(data, *stbl, "stss")
    sync = len(_entries(data, stss[0], ">I")) if stss else 0
    ctts = find(data, *stbl, "ctts")
    cmin = cmax = None
    if ctts:
        fmt = ">Ii" if data[ctts[0]] == 1 else ">II"
        vals = [o for _, o in _entries(data, ctts[0], fmt)]
        cmin, cmax = min(vals), max(vals)
    count = sum(c for c, _ in stts)
    total = sum(c * d for c, d in stts)
    if count != len(sizes):
        raise Mp4Error("stts and stsz sample counts differ")
    edts = find(data, s, e, "edts")
    elst = edits(data, edts) if edts else None
    delay = Fraction(0)
    start = None
    shown = Fraction(total, scale)
    if elst is not None:
        # Edit segment durations are in movie timescale units; media_time -1
        # marks an empty edit (a dwell on the presentation timeline).
        delay = Fraction(sum(d for d, mt in elst if mt == -1), movie_scale)
        starts = [mt for _, mt in elst if mt != -1]
        start = starts[0] if starts else None
        shown = Fraction(sum(d for d, _ in elst), movie_scale)
    return {
        "id": tid, "handler": handler, "enabled": bool(flags & 1), "timescale": scale,
        "media_duration": str(Fraction(total, scale)), "samples": count, "chunks": len(offsets),
        "bytes": sum(sizes), "first_offset": where[0] if where else None, "last_offset": where[-1] if where else None,
        "sync_samples": sync, "ctts_min": cmin, "ctts_max": cmax,
        "edit_delay": str(delay), "edit_media_start": start, "presentation_duration": str(shown),
    }


def movie(data):
    moov = find(data, 0, len(data), "moov")
    if moov is None:
        raise Mp4Error("no moov box")
    scale, dur = _time_header(data, find(data, *moov, "mvhd")[0])
    tracks = [track(data, s, e, scale) for k, s, e in boxes(data, *moov) if k == "trak"]
    return {"timescale": scale, "duration": str(Fraction(dur, scale)), "tracks": tracks}
