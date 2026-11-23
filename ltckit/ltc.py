"""Linear timecode (SMPTE ST 12-1 LTC) decoded from WAV audio."""
import struct

SYNC = [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1]   # bits 64..79 in the order they are recorded

# binary group flags (ST 12-1): BGF0 = bit 43, BGF1 = bit 58, BGF2 = bit 59
FORMATS = {(0, 0): "unspecified", (0, 1): "iso_8bit", (1, 0): "date_timezone", (1, 1): "page_line"}


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
            if tag == 0xFFFE:                       # WAVE_FORMAT_EXTENSIBLE: real tag starts the sub-format GUID
                tag, = struct.unpack_from("<H", body, 24)
            fmt = (tag, ch, rate, bits)
        elif cid == b"data":
            raw = body
        pos += 8 + size + (size & 1)                # chunks are word aligned
    if fmt is None or raw is None:
        raise LtcError("WAV file without fmt or data")
    tag, ch, rate, bits = fmt
    if tag != 1:
        raise LtcError("only PCM audio is supported")
    if bits not in (8, 16, 24):
        raise LtcError("only 8, 16 or 24-bit PCM is supported")
    width = bits // 8
    frames = len(raw) // (width * ch)
    chan = ch - 1                                   # cameras and recorders put LTC on the last channel
    samples = []
    for i in range(frames):
        off = (i * ch + chan) * width
        if width == 1:
            samples.append(raw[off] - 128)          # 8-bit PCM is unsigned
        else:
            samples.append(int.from_bytes(raw[off:off + width], "little", signed=True))
    return rate, ch, samples


def bits_from(samples):
    if not samples:
        raise LtcError("no LTC signal found")
    mean = sum(samples) / len(samples)              # tolerate a DC offset on the line
    edges, prev = [], samples[0] >= mean
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


def find_frames(bits):
    """Data bits (0..63) of every complete frame: 64 bits followed by the sync word."""
    out = []
    for i in range(80, len(bits) + 1):
        if bits[i - 16:i] == SYNC:
            out.append(bits[i - 80:i - 16])
    return out


def next_tc(t, fps, drop=False):
    hh, mm, ss, ff = t
    ff += 1
    if ff == fps:
        ff, ss = 0, ss + 1
        if ss == 60:
            ss, mm = 0, mm + 1
            if drop and fps == 30 and mm % 10:
                ff = 2                              # drop-frame skips frame numbers 00 and 01
            if mm == 60:
                mm, hh = 0, (hh + 1) % 24
    return (hh, mm, ss, ff)


def prev_tc(t, fps, drop=False):
    hh, mm, ss, ff = t
    if ff == 0 or (drop and fps == 30 and ss == 0 and ff == 2 and mm % 10):
        ff, ss = fps - 1, ss - 1
        if ss < 0:
            ss, mm = 59, mm - 1
            if mm < 0:
                mm, hh = 59, (hh - 1) % 24
    else:
        ff -= 1
    return (hh, mm, ss, ff)


def flags(b):
    bgf0, bgf2 = b[43], b[59]
    return FORMATS[(bgf2, bgf0)]


def _bcd(bits, start, n):
    return sum(bits[start + i] << i for i in range(n))


def _frame(b):
    ff = _bcd(b, 0, 4) + 10 * _bcd(b, 8, 2)
    ss = _bcd(b, 16, 4) + 10 * _bcd(b, 24, 3)
    mm = _bcd(b, 32, 4) + 10 * _bcd(b, 40, 3)
    hh = _bcd(b, 48, 4) + 10 * _bcd(b, 56, 2)
    user = 0
    for start in (60, 52, 44, 36, 28, 20, 12, 4):   # binary group 8 first, group 1 last
        user = (user << 4) | _bcd(b, start, 4)
    return (hh, mm, ss, ff), bool(b[10]), user      # bit 10 is the drop-frame flag


def decode(data):
    rate, channels, samples = read_wav(data)
    bits, cell = bits_from(samples)
    raws = find_frames(bits)
    reverse = False
    if not raws:                                    # timecode recorded or played backwards
        raws = find_frames(bits[::-1])[::-1]        # keep recording order
        reverse = True
    if not raws:
        raise LtcError("no complete LTC frame")
    fps_nominal = min((24, 25, 30), key=lambda f: abs(rate / cell / 80 - f))
    frames = [_frame(b) for b in raws]
    drop = frames[0][1]
    tcs = [f[0] for f in frames]
    step = prev_tc if reverse else next_tc
    gaps = sum(1 for a, b in zip(tcs, tcs[1:]) if b != step(a, fps_nominal, drop))
    sep = ";" if drop else ":"
    fmt = lambda t: "%02d:%02d:%02d%s%02d" % (t[0], t[1], t[2], sep, t[3])
    return {"sample_rate": rate, "channels": channels, "fps": "30000/1001" if drop and fps_nominal == 30 else str(fps_nominal),
            "drop_frame": drop, "reverse": reverse, "frames": len(frames),
            "first": fmt(tcs[0]), "last": fmt(tcs[-1]), "user_bits": "%08X" % frames[0][2],
            "color_frame": bool(raws[0][11]), "user_bits_format": flags(raws[0]), "gaps": gaps}
