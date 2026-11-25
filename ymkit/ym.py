"""Atari ST / AY-3-8910 register dumps (.ym): YM3, YM3b, YM5 and YM6 headers, digidrums, frames."""
import struct
from fractions import Fraction


class YmError(ValueError):
    pass


def cstring(data, p):
    try:
        end = data.index(b"\0", p)
    except ValueError:
        raise YmError("unterminated header string")
    return data[p:end].decode("latin-1") or None, end + 1


def _blank():
    return {"format": None, "frames": 0, "clock": 2000000, "rate": 50, "loop_frame": 0,
            "interleaved": False, "digidrums": 0, "digidrum_bytes": 0,
            "title": None, "author": None, "comment": None, "end_marker": None}


def _stats(out, data, start, regs):
    """Register statistics shared by every format. ``start`` is the first register byte."""
    n = out["frames"]
    interleaved = out["interleaved"]

    if len(data) < start + n * regs:
        raise YmError("register data is shorter than the frame count")

    def reg(frame, r):
        if interleaved:
            # all values of register r are stored consecutively, register by register
            return data[start + r * n + frame]
        # frame by frame: every frame holds its ``regs`` values consecutively
        return data[start + frame * regs + r]

    rate = out["rate"]
    out["duration"] = str(Fraction(n, rate)) if rate else None
    out["loop_seconds"] = str(Fraction(out["loop_frame"], rate)) if rate else None

    # register 13 (envelope shape) is only written when it is not 0xFF in YM5/YM6;
    # every frame writes it in YM3/YM3b
    if regs == 14:
        out["envelope_writes"] = n
    else:
        out["envelope_writes"] = sum(1 for f in range(n) if reg(f, 13) != 0xFF)

    # register 7 bits 0-2 enable tone A/B/C when cleared
    out["tone_frames"] = [sum(1 for f in range(n) if not (reg(f, 7) >> ch) & 1) for ch in range(3)]

    if n:
        period = ((reg(0, 1) & 0x0F) << 8) | reg(0, 0)
    else:
        period = 0
    out["first_tone_a"] = str(Fraction(out["clock"], 16 * period)) if period else None
    return out


def _parse_ym3(data):
    """YM3: bare frames of 14 registers; YM3b adds a LE32 loop frame at the end."""
    out = _blank()
    body = len(data)
    loop = 0
    if body % 14 == 4:
        loop = struct.unpack("<I", data[body - 4:])[0]
        body -= 4
        fmt = "YM3b"
    elif body % 14 == 0:
        fmt = "YM3!"
    else:
        raise YmError("not a YM register dump")
    n = body // 14
    if fmt == "YM3b" and loop >= n:
        raise YmError("loop frame is past the end of the tune")
    out.update(format=fmt, frames=n, clock=2000000, rate=50, loop_frame=loop,
               interleaved=False, end_marker=None)
    return _stats(out, data, 0, 14)


def _parse_modern(data, magic, regs=16):
    out = _blank()
    if len(data) < 34:
        raise YmError("file is shorter than the YM5/YM6 header")
    if data[4:12] != b"LeOnArD!":
        raise YmError("missing LeOnArD! signature")
    # YM5/YM6 headers and all their multi-byte fields are big-endian
    frames, attrs, drums, clock, rate, loop, extra = struct.unpack_from(">IIHIHIH", data, 12)
    p = 34 + extra

    # Digidrums come first; each sample size is a big-endian 32-bit word.
    drum_bytes = 0
    for _ in range(drums):
        if p + 4 > len(data):
            raise YmError("truncated digidrum table")
        size = struct.unpack_from(">I", data, p)[0]
        p += 4
        if p + size > len(data):
            raise YmError("truncated digidrum sample")
        drum_bytes += size
        p += size

    out["title"], p = cstring(data, p)
    out["author"], p = cstring(data, p)
    out["comment"], p = cstring(data, p)

    start = p
    out.update(format=magic.decode(), frames=frames, clock=clock, rate=rate, loop_frame=loop,
               interleaved=bool(attrs & 1), digidrums=drums, digidrum_bytes=drum_bytes)

    end = start + frames * regs
    if len(data) < end:
        raise YmError("register data is shorter than the frame count")
    out["end_marker"] = data[end:end + 4] == b"End!"
    return _stats(out, data, start, regs)


def parse(data):
    magic = data[:4]
    if magic in (b"YM5!", b"YM6!"):
        return _parse_modern(data, magic)
    # Genuine YM3/YM3b files carry no magic: just frames of 14 registers,
    # optionally followed by a little-endian 32-bit loop frame.
    return _parse_ym3(data)
