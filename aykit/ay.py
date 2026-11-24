"""ZX Spectrum / Amstrad AY music files (ZXAYEMUL): relative pointers, songs, song data, points, memory blocks."""
import struct
from fractions import Fraction


class AyError(ValueError):
    pass


def rel(data, at):
    """A relative pointer: signed big-endian 16-bit offset from the pointer field itself; 0 means none."""
    v, = struct.unpack_from(">h", data, at)
    return at + v if v else None


def cstr(data, at):
    if at is None or at >= len(data):
        return None
    end = data.find(b"\0", at)
    return data[at:end if end >= 0 else len(data)].decode("latin-1") or None


def parse(data):
    if data[:8] != b"ZXAYEMUL" or len(data) < 20:
        raise AyError("not a ZXAYEMUL file")
    out = {"file_version": data[8], "player_version": data[9],
           "author": cstr(data, rel(data, 12)), "misc": cstr(data, rel(data, 14)),
           "songs": data[16] + 1, "first_song": data[17], "song_list": []}
    base = rel(data, 18)
    for i in range(out["songs"] if base is not None else 0):
        s = base + 4 * i
        name = cstr(data, rel(data, s))
        d = rel(data, s + 2)
        a, b, c, noise = data[d:d + 4]
        length, fade = struct.unpack_from(">HH", data, d + 4)
        hi, lo = data[d + 8], data[d + 9]
        pts = rel(data, d + 10)
        stack, init, irq = struct.unpack_from(">HHH", data, pts) if pts is not None else (None, None, None)
        blocks, total = 0, 0
        addr_at = rel(data, d + 12)
        p = addr_at
        while p is not None and p + 6 <= len(data):
            addr, ln = struct.unpack_from(">HH", data, p)
            if addr == 0:
                break
            src = rel(data, p + 4)
            if src is not None:
                blocks += 1
                total += max(0, min(ln, 0x10000 - addr, len(data) - src))
            p += 6
        out["song_list"].append({"name": name, "channels": [a, b, c, noise],
                                 "length_seconds": str(Fraction(length, 50)),
                                 "fade_seconds": str(Fraction(fade, 50)),
                                 "hi_reg": hi, "lo_reg": lo,
                                 "stack": "$%04X" % stack if stack is not None else None,
                                 "init": "$%04X" % init if init is not None else None,
                                 "interrupt": "$%04X" % irq if irq is not None else None,
                                 "blocks": blocks, "block_bytes": total})
    return out
