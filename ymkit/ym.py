"""Atari ST / AY-3-8910 register dumps (.ym): YM3, YM3b, YM5 and YM6 headers, digidrums, frames."""
import struct
from fractions import Fraction


class YmError(ValueError):
    pass


def cstring(data, p):
    end = data.find(b"\0", p)
    if end < 0:
        raise YmError("unterminated header string")
    return data[p:end].decode("latin-1") or None, end + 1


def _parse_ym5(data, magic):
    """YM5!/YM6!: big-endian header, digidrums, Latin-1 strings, then frames."""
    if len(data) < 34:
        raise YmError("header is shorter than 34 bytes")
    if data[4:12] != b"LeOnArD!":
        raise YmError("missing LeOnArD! signature")
    frames, attrs, drums, clock, rate, loop, extra = struct.unpack_from(">IIHIHIH", data, 12)
    p = 34 + extra
    if len(data) < p:
        raise YmError("extra header data is missing")
    # each digidrum sample is a big-endian 32-bit size followed by that many bytes
    total = 0
    for _ in range(drums):
        if len(data) < p + 4:
            raise YmError("digidrum size is missing")
        size = struct.unpack_from(">I", data, p)[0]
        p += 4
        if len(data) < p + size:
            raise YmError("digidrum data is shorter than its size")
        p += size
        total += size
    out = {"format": magic.decode(), "frames": frames, "clock": clock, "rate": rate, "loop_frame": loop,
           "interleaved": bool(attrs & 1), "digidrums": drums, "digidrum_bytes": total, "end_marker": None}
    out["title"], p = cstring(data, p)
    out["author"], p = cstring(data, p)
    out["comment"], p = cstring(data, p)
    start = p
    if len(data) < start + frames * 16:
        raise YmError("register data is shorter than the frame count")
    out["end_marker"] = data[start + frames * 16:start + frames * 16 + 4] == b"End!"
    return out, start, 16


def _parse_ym3(data, magic):
    """YM3!/YM3b: 14 registers per frame, no header beyond the magic.

    YM3b adds the loop frame as a little-endian 32-bit number after the last
    frame.  The two variants share the ``YM3!`` magic and are told apart by
    the file size: a remainder of 4 bytes after removing the magic means the
    trailing loop frame is present.
    """
    body = len(data) - 4
    # files with the "YM3!" magic carry the loop frame when 4 bytes are left
    # over past whole frames; an explicit "YM3b" magic always has it
    if magic == b"YM3b" or body % 14 == 4:
        has_loop = True
        body -= 4
    else:
        has_loop = False
    if body % 14:
        raise YmError("register data is not a whole number of frames")
    frames = body // 14
    loop = struct.unpack_from("<I", data, 4 + frames * 14)[0] if has_loop else 0
    out = {"format": "YM3b" if has_loop else "YM3!", "frames": frames, "clock": 2000000, "rate": 50,
           "loop_frame": loop, "interleaved": False, "digidrums": 0, "digidrum_bytes": 0,
           "title": None, "author": None, "comment": None, "end_marker": None}
    return out, 4, 14


def parse(data):
    magic = data[:4]
    if magic in (b"YM5!", b"YM6!"):
        out, start, regs = _parse_ym5(data, magic)
    elif magic in (b"YM3!", b"YM3b"):
        out, start, regs = _parse_ym3(data, magic)
    else:
        raise YmError("not a YM register dump")
    n = out["frames"]
    # register-major ("interleaved") dumps store one whole register at a time
    if out["interleaved"]:
        reg = lambda f, r: data[start + r * n + f]
    else:
        reg = lambda f, r: data[start + f * regs + r]
    rate = out["rate"]
    out["duration"] = str(Fraction(n, rate)) if rate else None
    out["loop_seconds"] = str(Fraction(out["loop_frame"], rate)) if rate else None
    # register 13 (envelope shape) is only written when it is not 0xFF in YM5/YM6; every YM3 frame writes it
    out["envelope_writes"] = sum(1 for f in range(n) if regs == 14 or reg(f, 13) != 0xFF)
    # register 7 bits 0-2 enable tone A/B/C when cleared
    out["tone_frames"] = [sum(1 for f in range(n) if not reg(f, 7) >> ch & 1) for ch in range(3)]
    period = (reg(0, 1) & 0x0F) << 8 | reg(0, 0) if n else 0
    out["first_tone_a"] = str(Fraction(out["clock"], 16 * period)) if period else None
    return out
