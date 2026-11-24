"""Atari ST / AY-3-8910 register dumps (.ym): YM3, YM3b, YM5 and YM6 headers, digidrums, frames."""
import struct
from fractions import Fraction


class YmError(ValueError):
    pass


def cstring(data, p):
    try:
        end = data.index(b"\0", p)
    except ValueError as exc:
        raise YmError("unterminated string in YM header") from exc
    return data[p:end].decode("latin-1") or None, end + 1


def _digidrums(data, p, count):
    """Skip the YM5/YM6 digidrum block and return (new_offset, total_bytes)."""
    total = 0
    for _ in range(count):
        if p + 4 > len(data):
            raise YmError("truncated digidrum header")
        size = struct.unpack_from(">I", data, p)[0]
        p += 4
        if size > len(data) - p:
            raise YmError("truncated digidrum data")
        p += size
        total += size
    return p, total


def parse(data):
    magic = data[:4]
    out = {"format": None, "frames": 0, "clock": 2000000, "rate": 50, "loop_frame": 0, "interleaved": False,
           "digidrums": 0, "digidrum_bytes": 0, "title": None, "author": None, "comment": None, "end_marker": None}

    if magic in (b"YM5!", b"YM6!"):
        if len(data) < 34:
            raise YmError("truncated YM5/YM6 header")
        if data[4:12] != b"LeOnArD!":
            raise YmError("missing LeOnArD! signature")
        frames, attrs, drums, clock, rate, loop, extra = struct.unpack_from(">IIHIHIH", data, 12)
        p = 34 + extra
        if p > len(data):
            raise YmError("truncated YM extra header data")
        p, drum_bytes = _digidrums(data, p, drums)
        out["title"], p = cstring(data, p)
        out["author"], p = cstring(data, p)
        out["comment"], p = cstring(data, p)
        regs, start = 16, p
        out.update(format=magic.decode(), frames=frames, clock=clock, rate=rate, loop_frame=loop,
                   interleaved=bool(attrs & 1), digidrums=drums, digidrum_bytes=drum_bytes)
        out["end_marker"] = data[start + frames * regs:start + frames * regs + 4] == b"End!"

    elif magic in (b"YM3!", b"YM3b"):
        regs, start = 14, 4
        payload = len(data) - start

        if magic == b"YM3b":
            remainder = payload % regs
            if remainder == 4:
                loop = struct.unpack_from("<I", data, len(data) - 4)[0]
                frames = (payload - 4) // regs
            elif remainder == 0:
                # Also accept the variant marker without a trailing loop.
                loop = 0
                frames = payload // regs
            else:
                raise YmError("invalid YM3b length")
        else:
            frames, remainder = divmod(payload, regs)
            if remainder == 4:
                # YM3b files commonly keep the YM3! magic and append a
                # little-endian loop frame.
                loop = struct.unpack_from("<I", data, len(data) - 4)[0]
                magic = b"YM3b"
            elif remainder == 0:
                loop = 0
            else:
                raise YmError("YM3 register data has an invalid length")

        out.update(format=magic.decode(), frames=frames, loop_frame=loop, interleaved=False)

    else:
        raise YmError("not a YM register dump")

    n = out["frames"]
    if len(data) < start + n * regs:
        raise YmError("register data is shorter than the frame count")

    interleaved = out["interleaved"]

    def reg(f, r):
        if interleaved:
            # Register-by-register: all n values for register 0, then ...
            offset = start + r * n + f
        else:
            # Frame-by-frame: r values within each 14/16-byte frame.
            offset = start + f * regs + r
        return data[offset]

    out["duration"] = str(Fraction(n, out["rate"])) if out["rate"] else None
    out["loop_seconds"] = str(Fraction(out["loop_frame"], out["rate"])) if out["rate"] else None
    # register 13 (envelope shape) is only written when it is not 0xFF in YM5/YM6; every frame writes it in YM3
    out["envelope_writes"] = sum(1 for f in range(n) if regs == 14 or reg(f, 13) != 0xFF)
    # register 7 bits 0-2 enable tone A/B/C when cleared
    out["tone_frames"] = [sum(1 for f in range(n) if ((reg(f, 7) >> ch) & 1) == 0) for ch in range(3)]
    tp = lambda f: (reg(f, 1) & 0x0F) << 8 | reg(f, 0)
    out["first_tone_a"] = str(Fraction(out["clock"], 16 * tp(0))) if n and tp(0) else None
    return out
