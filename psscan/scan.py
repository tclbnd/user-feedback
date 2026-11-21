"""Walk a program stream and summarise its elementary streams."""
from fractions import Fraction

from .headers import PsError, pack, pes


def _kind(sid):
    if sid == 0xBD:
        return "private1"
    if sid == 0xBE:
        return "padding"
    if sid == 0xBF:
        return "private2"
    if 0xC0 <= sid <= 0xDF:
        return "audio"
    if 0xE0 <= sid <= 0xEF:
        return "video"
    return "other"


def scan(b):
    pos, version, packs, scrs, mux = 0, None, 0, [], None
    streams, padding = {}, 0
    while pos + 4 <= len(b):
        if b[pos:pos + 3] != b"\x00\x00\x01":
            pos += 1
            continue
        code = b[pos + 3]
        if code == 0xB9:
            break
        if code == 0xBA:
            v, scr, rate, hl = pack(b, pos)
            version = version or v
            packs += 1
            scrs.append(scr)
            mux = mux if mux is not None else rate
            pos += hl
            continue
        if code < 0xBB:
            pos += 4
            continue
        length = int.from_bytes(b[pos + 4:pos + 6], "big")
        start, end = pos + 6, pos + 6 + length
        if end > len(b):
            break
        if code == 0xBB:
            pos = end
            continue
        pts, dts, ps = pes(b, start, end, version or 2)
        key = "0x%02x" % code
        s = streams.setdefault(key, {"kind": _kind(code), "packets": 0, "payload_bytes": 0, "first_pts": None, "last_pts": None})
        s["packets"] += 1
        s["payload_bytes"] += end - ps
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
        "format": "MPEG-%d" % version, "packs": packs, "mux_rate": mux,
        "first_scr": str(Fraction(scrs[0], 27000000)), "scr_span": str(Fraction(scrs[-1] - scrs[0], 27000000)),
        "padding_bytes": padding, "streams": dict(sorted(streams.items())),
    }
