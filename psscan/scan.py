"""Walk a program stream and summarise its elementary streams."""
from fractions import Fraction

from .headers import PsError, dvd_substream, pack, pes

PADDING_STREAM = 0xBE
PRIVATE_STREAM_1 = 0xBD
SYSTEM_HEADER = 0xBB
PACK_HEADER = 0xBA
PROGRAM_END = 0xB9


def _kind(sid):
    if sid == PRIVATE_STREAM_1:
        return "private1"
    if sid == 0xBF:
        return "private2"
    if 0xC0 <= sid <= 0xDF:
        return "audio"
    if 0xE0 <= sid <= 0xEF:
        return "video"
    return "other"


def scan(b):
    pos, version, packs = 0, None, 0
    first_scr = last_scr = mux = None
    streams, padding = {}, 0
    n = len(b)
    while pos + 4 <= n:
        if b[pos:pos + 3] != b"\x00\x00\x01":
            pos += 1
            continue
        code = b[pos + 3]
        if code == PROGRAM_END:
            break
        if code == PACK_HEADER:
            v, scr, rate, header_length = pack(b, pos)
            if version is None:
                version, mux, first_scr = v, rate, scr
            packs += 1
            last_scr = scr
            pos += header_length
            continue
        if code < SYSTEM_HEADER:
            pos += 4
            continue
        length = int.from_bytes(b[pos + 4:pos + 6], "big")
        start = pos + 6
        if length:
            end = start + length
        else:
            # unbounded packet: runs to the next start code
            end = b.find(b"\x00\x00\x01", start)
            if end < 0:
                end = n
        if end > n:
            break
        if code == SYSTEM_HEADER:
            pos = end
            continue
        pts, dts, payload = pes(b, start, end, version or 2)
        if code == PADDING_STREAM:
            padding += end - payload
            pos = end
            continue
        key = "0x%02x" % code
        kind = _kind(code)
        lpcm = None
        if code == PRIVATE_STREAM_1 and payload < end:
            sub, kind, sub_length, lpcm = dvd_substream(b, payload, end)
            key = "0xbd:0x%02x" % sub
            payload += sub_length
        s = streams.setdefault(key, {
            "kind": kind, "packets": 0, "payload_bytes": 0,
            "first_pts": None, "last_pts": None,
        })
        if lpcm is not None and "bits" not in s:
            s.update(lpcm)
        s["packets"] += 1
        s["payload_bytes"] += end - payload
        if pts is not None:
            if s["first_pts"] is None:
                s["first_pts"] = pts
            s["last_pts"] = pts
        pos = end
    if not packs:
        raise PsError("no pack headers")
    for s in streams.values():
        for k in ("first_pts", "last_pts"):
            if s[k] is not None:
                s[k] = str(Fraction(s[k], 90000))
    return {
        "format": "MPEG-%d" % version,
        "packs": packs,
        "mux_rate": mux,
        "first_scr": str(Fraction(first_scr, 27000000)),
        "scr_span": str(Fraction(last_scr - first_scr, 27000000)),
        "padding_bytes": padding,
        "streams": dict(sorted(streams.items())),
    }
