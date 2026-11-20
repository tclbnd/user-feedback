"""Whole-stream summary."""
from fractions import Fraction

from .syncframe import SyncError, parse

SYNC = b"\x0b\x77"


def _frames(data):
    """Yield complete sync frames, skipping corrupt bytes and a truncated tail."""
    pos = 0
    while pos + 6 <= len(data):
        try:
            f = parse(data, pos)
        except SyncError:
            nxt = data.find(SYNC, pos + 1)
            if nxt < 0:
                return
            pos = nxt
            continue
        except IndexError:
            return  # truncated header
        candidates = f.get("sizes") or [f["size"]]
        size = next((s for s in candidates
                     if pos + s == len(data) or data[pos + s:pos + s + 2] == SYNC), 0)
        if not size:
            if pos + candidates[0] > len(data):
                return  # incomplete final frame: keep the frames counted so far
            size = candidates[0]  # damaged stream: trust the header, resync afterwards
        f["size"] = size
        f.pop("sizes", None)
        yield f
        pos += size


def _eac3_summary(frames):
    # A programme is one independent substream plus its dependent substreams;
    # dependent substreams only add channels, not samples or duration.
    indep = next((f for f in frames if f["strmtyp"] != 1), frames[0])
    prog = [f for f in frames if f["strmtyp"] != 1 and f["substreamid"] == indep["substreamid"]]
    deps = {}
    for f in frames:
        if f["strmtyp"] == 1:
            deps.setdefault(f["substreamid"], []).append(f)
    rate = indep["sample_rate"]
    samples = sum(f["samples"] for f in prog)

    def bitrate(sub):
        return Fraction(8 * rate * sum(f["size"] for f in sub), sum(f["samples"] for f in sub))

    total = bitrate(prog) + sum((bitrate(sub) for sub in deps.values()), Fraction(0))
    return {"codec": "eac3", "sample_rate": rate,
            "channels": indep["channels"] + sum(sub[0]["channels"] for sub in deps.values()),
            "acmod": indep["acmod"],
            "lfe": indep["lfe"] or any(sub[0]["lfe"] for sub in deps.values()),
            "bitrate": round(total), "dialnorm": indep["dialnorm"],
            "frames": len(frames), "samples": samples,
            "duration": str(Fraction(samples, rate))}


def scan(data):
    frames = list(_frames(data))
    if not frames:
        raise SyncError("no sync frames")
    first = frames[0]
    if first["codec"] == "eac3":
        return _eac3_summary(frames)
    samples = sum(f["samples"] for f in frames)
    return {"codec": "ac3", "sample_rate": first["sample_rate"], "channels": first["channels"],
            "acmod": first["acmod"], "lfe": first["lfe"], "bitrate": first["bitrate"],
            "dialnorm": first["dialnorm"], "frames": len(frames), "samples": samples,
            "duration": str(Fraction(samples, first["sample_rate"]))}
