"""Atari ST / AY-3-8910 register dumps (.ym): YM3, YM3b, YM5 and YM6 headers, digidrums, frames."""
import struct
from fractions import Fraction


class YmError(ValueError):
    pass


def cstring(data, p):
    end = data.index(b"\0", p)
    return data[p:end].decode("latin-1") or None, end + 1


def parse(data):
    magic = data[:4]
    out = {"format": None, "frames": 0, "clock": 2000000, "rate": 50, "loop_frame": 0, "interleaved": True,
           "digidrums": 0, "digidrum_bytes": 0, "title": None, "author": None, "comment": None, "end_marker": None}
    if magic in (b"YM5!", b"YM6!"):
        if data[4:12] != b"LeOnArD!":
            raise YmError("missing LeOnArD! signature")
        frames, attrs, drums, clock, rate, loop, extra = struct.unpack_from("<IIHIHIH", data, 12)
        p = 34 + extra
        total = 0
        out["title"], p = cstring(data, p)
        out["author"], p = cstring(data, p)
        out["comment"], p = cstring(data, p)
        regs, start = 16, p
        out.update(format=magic.decode(), frames=frames, clock=clock, rate=50, loop_frame=loop,
                   interleaved=bool(attrs & 1), digidrums=drums, digidrum_bytes=total)
        out["end_marker"] = data[start + frames * regs:start + frames * regs + 4] == b"End!"
    else:
        raise YmError("not a YM register dump")
    n = out["frames"]
    if len(data) < start + n * regs:
        raise YmError("register data is shorter than the frame count")
    reg = lambda f, r: data[start + f * regs + r]
    out["duration"] = str(Fraction(n, out["rate"])) if out["rate"] else None
    out["loop_seconds"] = str(Fraction(out["loop_frame"], out["rate"])) if out["rate"] else None
    # register 13 (envelope shape) is only written when it is not 0xFF in YM5/YM6; every frame writes it in YM3
    out["envelope_writes"] = sum(1 for f in range(n) if regs == 14 or reg(f, 13) != 0xFF)
    # register 7 bits 0-2 enable tone A/B/C when cleared
    out["tone_frames"] = [sum(1 for f in range(n) if not reg(f, 7) >> ch & 1) for ch in range(3)]
    tp = lambda f: (reg(f, 1) & 0x0F) << 8 | reg(f, 0)
    out["first_tone_a"] = str(Fraction(out["clock"], 16 * tp(0))) if n and tp(0) else None
    return out
