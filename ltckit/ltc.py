"""Linear timecode (SMPTE ST 12-1 LTC) decoded from WAV audio."""
import struct

SYNC = [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1]   # bits 64..79 in the order they are recorded


class LtcError(ValueError):
    pass


def read_wav(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise LtcError("not a WAV file")
    pos, fmt, raw = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            tag, ch, rate = struct.unpack_from("<HHI", body)
            bits, = struct.unpack_from("<H", body, 14)
            fmt = (tag, ch, rate, bits)
        elif cid == b"data":
            raw = body
        pos += 8 + size
    if fmt is None or raw is None:
        raise LtcError("WAV file without fmt or data")
    tag, ch, rate, bits = fmt
    if tag != 1 or bits != 16:
        raise LtcError("only 16-bit PCM is supported")
    width = bits // 8
    frames = len(raw) // (width * ch)
    chan = 0
    samples = []
    for i in range(frames):
        off = (i * ch + chan) * width
        samples.append(int.from_bytes(raw[off:off + width], "little", signed=True))
    return rate, ch, samples


def bits_from(samples):
    mean = 0
    edges, prev = [], samples[0] >= mean if samples else False
    for i, s in enumerate(samples):
        cur = s >= mean
        if cur != prev:
            edges.append(i)
            prev = cur
    gaps = [b - a for a, b in zip(edges, edges[1:])]
    if len(gaps) < 40:
        raise LtcError("no LTC signal found")
    full = sorted(gaps)[len(gaps) * 9 // 10]              # long gaps are whole bit cells
    out, half = [], False
    for g in gaps:
        if g > full * 0.75:
            out.append(0)
            half = False
        elif half:
            out.append(1)
            half = False
        else:
            half = True
    return out, full


def _bcd(bits, start, n):
    return sum(bits[start + i] << i for i in range(n))


def _frame(b):
    ff = _bcd(b, 0, 4) + 10 * _bcd(b, 8, 2)
    ss = _bcd(b, 16, 4) + 10 * _bcd(b, 24, 3)
    mm = _bcd(b, 32, 4) + 10 * _bcd(b, 40, 3)
    hh = _bcd(b, 48, 4) + 10 * _bcd(b, 56, 2)
    user = 0
    for start in (4, 12, 20, 28, 36, 44, 52, 60):
        user = (user << 4) | _bcd(b, start, 4)
    return (hh, mm, ss, ff), False, user


def decode(data):
    rate, channels, samples = read_wav(data)
    bits, cell = bits_from(samples)
    fwd = []
    for i in range(16, len(bits) + 1):
        if bits[i - 16:i] == SYNC:
            fwd.append((bits[max(0, i - 80):i - 16] + [0] * 64)[:64] if i < 80 else bits[i - 80:i - 16])
    reverse = False
    frames = [_frame(b) for b in fwd]
    if not frames:
        raise LtcError("no complete LTC frame")
    fps_nominal = min((24, 25, 30), key=lambda f: abs(rate / cell / 80 - f))
    drop = frames[0][1]
    tcs = [f[0] for f in frames]
    sep = ";" if drop else ":"
    fmt = lambda t: "%02d:%02d:%02d%s%02d" % (t[0], t[1], t[2], sep, t[3])
    return {"sample_rate": rate, "channels": channels, "fps": "30000/1001" if drop and fps_nominal == 30 else str(fps_nominal),
            "drop_frame": drop, "reverse": reverse, "frames": len(frames),
            "first": fmt(tcs[0]), "last": fmt(tcs[-1]), "user_bits": "%08X" % frames[0][2]}
