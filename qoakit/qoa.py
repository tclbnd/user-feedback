"""QOA (Quite OK Audio) files: header, frames, LMS-predicted 3-bit slices (qoaformat.org)."""
import struct
import zlib
from fractions import Fraction

SCALEFACTOR = [round((s + 1) ** 2.75) for s in range(16)]
DEQUANT_BASE = (0.75, -0.75, 2.5, -2.5, 4.5, -4.5, 7, -7)


DEQUANT = [[int(SCALEFACTOR[s] * d) for d in DEQUANT_BASE] for s in range(16)]
SLICE = 20


class QoaError(ValueError):
    pass


def _clamp16(v):
    return -32768 if v < -32768 else 32767 if v > 32767 else v


def _s16(v):
    return v - 65536 if v & 0x8000 else v


def decode_frame(data, pos):
    if pos + 8 > len(data):
        raise QoaError("truncated frame header")
    ch = data[pos]
    rate, = struct.unpack_from(">H", data, pos + 2)
    fsamples, fsize = struct.unpack_from(">HH", data, pos + 4)
    if ch == 0 or pos + fsize > len(data):
        raise QoaError("truncated or invalid frame")
    slices = -(-fsamples // SLICE)
    if fsize != 8 + 16 * ch + 8 * slices * ch:
        raise QoaError("frame size does not match its samples")
    p = pos + 8
    hist, weights = [], []
    for c in range(ch):
        hist.append([_s16(int.from_bytes(data[p + 2 * i:p + 2 * i + 2], "big")) for i in range(4)])
        weights.append([_s16(int.from_bytes(data[p + 8 + 2 * i:p + 10 + 2 * i], "big")) for i in range(4)])
        p += 16
    out = [[] for _ in range(ch)]
    for c in range(ch):
        for si in range(slices):
            word = int.from_bytes(data[p:p + 8], "big")
            p += 8
            sf = word >> 60
            for k in range(SLICE):
                q = (word >> (57 - 3 * k)) & 7
                h, w = hist[c], weights[c]
                pred = (w[0] * h[0] + w[1] * h[1] + w[2] * h[2] + w[3] * h[3]) >> 13
                r = DEQUANT[sf][q]
                s = _clamp16(pred + r)
                delta = r >> 4
                for i in range(4):
                    w[i] += -delta if h[i] < 0 else delta
                h.pop(0)
                h.append(s)
                out[c].append(s)
    return {"channels": ch, "rate": rate, "samples": len(out[0]), "pcm": out}, pos + fsize


def parse(data):
    if data[:4] != b"qoaf" or len(data) < 8:
        raise QoaError("not a QOA file")
    total, = struct.unpack_from(">I", data, 4)
    pos, frames, decoded, peak, first = 8, 0, 0, 0, None
    crc = 0
    while pos < len(data):
        f, pos = decode_frame(data, pos)
        first = first or f
        if (f["channels"], f["rate"]) != (first["channels"], first["rate"]) and total:
            raise QoaError("channels or sample rate change in a non-streaming file")
        frames += 1
        decoded += f["samples"]
        inter = bytearray()
        for k in range(f["samples"]):
            for c in range(f["channels"]):
                v = f["pcm"][c][k]
                peak = max(peak, abs(v))
                inter += struct.pack("<h", v)
        crc = zlib.crc32(inter, crc)
    if first is None:
        raise QoaError("no frames")
    samples = total
    return {"streaming": total == 0, "channels": first["channels"], "sample_rate": first["rate"],
            "samples": samples, "frames": frames, "decoded_samples": decoded,
            "duration": str(Fraction(samples, first["rate"])), "peak": peak,
            "pcm_crc32": "%08x" % (crc & 0xFFFFFFFF)}
