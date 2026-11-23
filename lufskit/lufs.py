"""Programme loudness of WAV files per ITU-R BS.1770-4 / EBU R 128."""
import math
import struct

# K-weighting pre-filter at 48 kHz (BS.1770-4, table 1)
SHELF = (1.53512485958697, -2.69169618940638, 1.19839281085285, -1.69065929318241, 0.73248077421585)


class LufsError(ValueError):
    pass


def read_wav(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise LufsError("not a WAV file")
    p, fmt, body = 12, None, None
    while p + 8 <= len(data):
        cid, size = data[p:p + 4], struct.unpack_from("<I", data, p + 4)[0]
        if cid == b"fmt ":
            tag, ch, rate, _, _, bits = struct.unpack_from("<HHIIHH", data, p + 8)
            if tag == 0xFFFE and size >= 40:
                tag = struct.unpack_from("<H", data, p + 32)[0]
            fmt = (tag, ch, rate, bits)
        elif cid == b"data":
            body = data[p + 8:p + 8 + size]
        p += 8 + size + (size & 1)
    if fmt is None or body is None:
        raise LufsError("WAV file without fmt or data chunk")
    tag, ch, rate, bits = fmt
    n = len(body) // (bits // 8 * ch)
    if tag == 3 and bits == 32:
        flat = struct.unpack("<%df" % (n * ch), body[:n * ch * 4])
    elif tag == 1 and bits == 16:
        flat = [v / 32768 for v in struct.unpack("<%dh" % (n * ch), body[:n * ch * 2])]
    else:
        raise LufsError("unsupported sample format")
    return rate, [flat[c::ch] for c in range(ch)]


def biquad(x, c):
    b0, b1, b2, a1, a2 = c
    y, x1, x2, y1, y2 = [], 0.0, 0.0, 0.0, 0.0
    for v in x:
        out = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, v, y1, out
        y.append(out)
    return y


def block_powers(sq, size, step):
    acc = [0.0]
    for v in sq:
        acc.append(acc[-1] + v)
    return [(acc[s + size] - acc[s]) / size for s in range(0, len(sq) - size + 1, step)]


def loud(power):
    return -0.691 + 10 * math.log10(power) if power > 0 else -math.inf


def fmt(v):
    return None if v is None or v == -math.inf else "%.1f" % v


def parse(data):
    rate, chans = read_wav(data)
    weighted = []
    for x in chans:
        y = biquad(x, SHELF)
        weighted.append((1.0, [v * v for v in y]))
    peak = max((abs(v) for x in chans for v in x), default=0.0)
    out = {"sample_rate": rate, "channels": len(chans), "measured_channels": len(weighted),
           "duration": "%.3f" % (len(chans[0]) / rate if chans else 0),
           "sample_peak": fmt(20 * math.log10(peak)) if peak > 0 else None}
    m, s = int(round(0.4 * rate)), int(round(3 * rate))

    def blocks(size):
        per = [[g * v for v in block_powers(sq, size, size)] for g, sq in weighted]
        return [sum(col) for col in zip(*per)] if per else []
    mom = blocks(m)
    short = blocks(s)
    out["momentary_max"] = fmt(max((loud(p) for p in mom), default=None))
    out["short_term_max"] = fmt(max((loud(p) for p in short), default=None))
    gated = [p for p in mom if loud(p) > -70]
    out["integrated"] = fmt(loud(sum(gated) / len(gated)) if gated else None)
    return out
