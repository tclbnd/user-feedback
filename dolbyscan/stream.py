"""Whole-stream summary."""
from fractions import Fraction

from .syncframe import SyncError, parse


def scan(data):
    pos, frames = 0, []
    while pos + 8 <= len(data):
        f = parse(data, pos)
        frames.append(f)
        pos += f["size"]
    if not frames:
        raise SyncError("no sync frames")
    first = frames[0]
    samples = sum(f["samples"] for f in frames)
    return {"codec": first["codec"], "sample_rate": first["sample_rate"], "channels": first["channels"],
            "acmod": first["acmod"], "lfe": first["lfe"], "bitrate": first["bitrate"], "dialnorm": first["dialnorm"],
            "frames": len(frames), "samples": samples, "duration": str(Fraction(samples, first["sample_rate"]))}
