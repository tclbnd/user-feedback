"""Gravis UltraSound GF1 patches (.pat): headers, sample headers, sample data."""
import math
import struct
import zlib


class PatchError(ValueError):
    pass


def _key(hz):
    return round(69 + 12 * math.log2(hz / 440)) if hz > 0 else None


def parse(data):
    if len(data) < 239 or data[:8] != b"GF1PATCH" or data[8:12] not in (b"110\0", b"100\0") or data[12:22] != b"ID#000002\0":
        raise PatchError("not a GF1 patch")
    desc = data[22:82].split(b"\0")[0].decode("latin-1")
    count = data[198]
    pos, samples, complete = 239, [], True
    for _ in range(count):
        if pos + 96 > len(data):
            complete = False
            break
        h = data[pos:pos + 96]
        frac = h[7]
        size, ls, le, rate, low, high, root, tune, balance = struct.unpack_from("<iiiHiiihB", h, 8)
        modes = h[55]
        raw = data[pos + 96:pos + 96 + size]
        if len(raw) < size:
            complete = False
            break
        bits = 16 if modes & 1 else 8
        width = bits // 8
        signed = bool(modes & 2)
        if bits == 16:
            vals = list(struct.unpack("<%dh" % (size // 2), raw[:size // 2 * 2])) if signed else [v - 32768 for v in struct.unpack("<%dH" % (size // 2), raw[:size // 2 * 2])]
        else:
            vals = [v - 256 if v > 127 else v for v in raw] if signed else [v - 128 for v in raw]
        loop = "off" if not modes & 4 else ("pingpong" if modes & 16 else "forward")
        samples.append({
            "name": h[:7].split(b"\0")[0].decode("latin-1"), "bits": bits, "signed": signed, "loop": loop,
            "reverse": bool(modes & 8), "rate": rate, "frames": len(vals),
            "loop_start": ls + (frac >> 4) / 16, "loop_end": le + (frac & 15) / 16,
            "root_key": _key(root), "low_key": _key(low), "high_key": _key(high), "balance": balance,
            "peak": max((abs(v) for v in vals), default=0),
            "crc32": "%08x" % zlib.crc32(struct.pack("<%dh" % len(vals), *vals))})
        pos += 96 + len(vals)
    return {"description": desc, "samples": samples, "complete": complete}
