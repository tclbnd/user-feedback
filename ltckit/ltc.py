"""Linear timecode (SMPTE ST 12-1 LTC) decoded from WAV audio.

An LTC frame is 80 bits, bit 0 being recorded first:

    bit(s)  field                       bit(s)  field
    0..3    frame units                 40..42  minute tens
    4..7    user bits, group 1          43      binary group flag 1
    8..9    frame tens                  44..47  user bits, group 6
    10      drop frame flag             48..51  hour units
    11      colour frame flag           52..55  user bits, group 7
    12..15  user bits, group 2          56..57  hour tens
    16..19  second units                58      binary group flag 2
    20..23  user bits, group 3          59      unassigned
    24..26  second tens                 60..63  user bits, group 8
    27      binary group flag 0         64..79  sync word
    28..31  user bits, group 4                  0011 1111 1111 1101
    32..35  minute units
    36..39  user bits, group 5

Every bit cell starts with a transition and a 1 has an extra transition in
the middle, so the signal alternates between half-cell and whole-cell gaps.
"""
import array
import struct
import sys

SYNC = [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1]   # bits 64..79 in the order they are recorded
_SYNC_BYTES = bytes(SYNC)
_PCM_SUBFORMAT = bytes.fromhex("0100000000001000800000aa00389b71")
_USER_OFFSETS = (4, 12, 20, 28, 36, 44, 52, 60)            # binary group 1 .. group 8
_U8_TO_SIGNED = bytes((v + 128) & 0xFF for v in range(256))
_SIGN_BYTE = bytes(0xFF if v & 0x80 else 0x00 for v in range(256))


class LtcError(ValueError):
    pass


def _chunks(data):
    """Yield (id, body) for every RIFF chunk, honouring the pad byte."""
    pos, end = 12, len(data)
    while pos + 8 <= end:
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        yield cid, data[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)


def _samples_of(raw, width, stride, chan):
    """The samples of one channel as a signed integer array."""
    data = bytearray(len(raw) // stride * width)
    for k in range(width):                                 # de-interleave the channel byte-wise
        data[k::width] = raw[chan * width + k::stride]
    if width == 1:                                         # 8-bit PCM is unsigned, centred on 128
        return array.array("b", data.translate(_U8_TO_SIGNED))
    if width in (2, 4):
        out = array.array("h" if width == 2 else "i")
        out.frombytes(bytes(data))
    else:                                                  # 24-bit: sign-extend into 32-bit words
        wide = bytearray(len(data) // 3 * 4)
        wide[0::4] = data[0::3]
        wide[1::4] = data[1::3]
        wide[2::4] = data[2::3]
        wide[3::4] = data[2::3].translate(_SIGN_BYTE)
        out = array.array("i")
        out.frombytes(bytes(wide))
    if sys.byteorder == "big":
        out.byteswap()
    return out


def _wav_info(data):
    """Return (sample rate, channel count, raw sample bytes, sample width in bytes)."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise LtcError("not a WAV file")
    fmt, raw = None, None
    for cid, body in _chunks(data):
        if cid == b"fmt " and fmt is None:
            fmt = body
        elif cid == b"data" and raw is None:
            raw = body
    if fmt is None or raw is None or len(fmt) < 16:
        raise LtcError("WAV file without fmt or data")
    tag, ch, rate, _byte_rate, _block_align, bits = struct.unpack_from("<HHIIHH", fmt)
    if tag == 0xFFFE:
        # WAVE_FORMAT_EXTENSIBLE: the real format is in the sub-format GUID
        if len(fmt) < 40 or fmt[24:40] != _PCM_SUBFORMAT:
            raise LtcError("unsupported WAVE_FORMAT_EXTENSIBLE sub-format")
    elif tag != 1:
        raise LtcError("only PCM WAV files are supported")
    if bits not in (8, 16, 24, 32):
        raise LtcError("unsupported sample width")
    if ch < 1 or rate <= 0:
        raise LtcError("invalid WAV header")
    return rate, ch, raw, bits // 8


def read_wav(data):
    """Sample rate, channel count and the samples of the LTC channel."""
    rate, channels, raw, width = _wav_info(data)
    return rate, channels, _pick_channel(rate, channels, raw, width)


def _gaps(samples):
    """Distance in samples between successive transitions of the biphase signal."""
    if not samples:
        raise LtcError("no LTC signal found")
    low, high = min(samples), max(samples)
    middle = (low + high) / 2.0
    # a little hysteresis keeps noise around the threshold from adding transitions
    step = max((high - low) // 8, 0)
    edges, level = [], 0
    for i, s in enumerate(samples):
        if level <= 0 and s > middle + step:
            edges.append(i)
            level = 1
        elif level >= 0 and s < middle - step:
            edges.append(i)
            level = -1
    return [b - a for a, b in zip(edges, edges[1:])]


def _cell_lengths(gaps):
    """Split the gaps into (half cell, whole cell) lengths."""
    ordered = sorted(gaps)
    trim = len(ordered) // 100                              # a cut-off cell or a glitch at either end
    body = ordered[trim:len(ordered) - trim] if trim else ordered
    if len(body) < 2:
        body = ordered
    lo, hi = float(body[0]), float(body[-1])
    if hi <= lo:
        raise LtcError("no LTC signal found")
    c1, c2 = lo, hi
    for _ in range(16):
        s1 = [g for g in body if abs(g - c1) <= abs(g - c2)]
        s2 = [g for g in body if abs(g - c1) > abs(g - c2)]
        if not s1 or not s2:
            break
        n1, n2 = sum(s1) / len(s1), sum(s2) / len(s2)
        if n1 > n2:
            n1, n2 = n2, n1
        if (n1, n2) == (c1, c2):
            break
        c1, c2 = n1, n2
    if c2 / c1 < 1.4:
        raise LtcError("no LTC signal found")
    return c1, c2


def _bits_of(gaps, threshold, skip_first=False):
    """Turn gap lengths into bits: a whole cell is 0, a pair of half cells is 1."""
    out, opened = [], False
    for g in gaps[1 if skip_first else 0:]:
        if g > threshold:                                  # a whole cell: the bit is 0
            out.append(0)
            opened = False
        elif opened:                                       # second half cell: the bit is 1
            out.append(1)
            opened = False
        else:                                              # first half cell: wait for its pair
            opened = True
    return out


def bits_from(samples):
    """Recover the recorded bits; also returns the length of a whole bit cell."""
    gaps = _gaps(samples)
    if len(gaps) < 40:
        raise LtcError("no LTC signal found")
    half, full = _cell_lengths(gaps)
    threshold = (half + full) / 2
    # the recording may begin on either half of a 1 bit's cell, so try both readings
    # and keep the one that shows more sync words
    candidates = [_bits_of(gaps, threshold), _bits_of(gaps, threshold, skip_first=True)]
    best = max(candidates, key=lambda b: bytes(b).count(_SYNC_BYTES))
    return best, full


def _field(bits, start, n):
    return sum(bits[start + i] << i for i in range(n))


def _frame(b, fps):
    """Decode the 64 data bits of one frame, or None if they are not a valid timecode."""
    fu, ft = _field(b, 0, 4), _field(b, 8, 2)
    su, st = _field(b, 16, 4), _field(b, 24, 3)
    mu, mt = _field(b, 32, 4), _field(b, 40, 3)
    hu, ht = _field(b, 48, 4), _field(b, 56, 2)
    if fu > 9 or su > 9 or mu > 9 or hu > 9:                # not BCD
        return None
    if ft > 2 or st > 5 or mt > 5 or ht > 2:                # tens digit out of range
        return None
    ff, ss, mm, hh = ft * 10 + fu, st * 10 + su, mt * 10 + mu, ht * 10 + hu
    if ff >= fps or ss > 59 or mm > 59 or hh > 23:
        return None
    user = 0
    for start in reversed(_USER_OFFSETS):                  # group 8 first, group 1 last
        user = (user << 4) | _field(b, start, 4)
    return (hh, mm, ss, ff), bool(b[10]), user


def _scan(bits, fps):
    """Every complete frame in the bit stream, in stream order.

    Frames are exactly 80 bits apart, so in the recovered bit stream their sync
    words all share the same position modulo 80.  Grouping by that phase throws
    out sync words that merely happen to occur inside the timecode data.
    """
    stream = bytes(bits)
    ends, i = [], stream.find(_SYNC_BYTES)
    while i != -1:
        ends.append(i + len(SYNC))                         # index just past the sync word
        i = stream.find(_SYNC_BYTES, i + 1)
    if not ends:
        return []
    phases = {}
    for end in ends:
        frame = _frame(stream[end - 80:end - 16], fps) if end >= 80 else None
        if frame is not None:
            phases.setdefault(end % 80, []).append(frame)
    if not phases:
        return []
    return max(phases.values(), key=len)


def _frames_of(bits, fps):
    """Frames in file order, and whether the LTC was recorded/played backwards.

    A take recorded forwards decodes straight from the bit stream.  If the audio
    was reversed, sync words only line up in the reversed stream, so the frames
    found there are put back into file order and `reverse` is set.
    """
    fwd = _scan(bits, fps)
    rev = _scan(bits[::-1], fps)
    if len(rev) > len(fwd):
        return rev[::-1], True
    return fwd, False


def _pick_channel(rate, channels, raw, width):
    """The samples of the last channel, which is where LTC is normally recorded."""
    samples = _samples_of(raw, width, width * channels, channels - 1)
    if not samples or max(samples) == min(samples):
        raise LtcError("no LTC signal found")
    return samples


def _fps_from_cell(rate, cell):
    """The nominal timecode rate: 24, 25 or 30 (drop-frame counts as 30)."""
    nominal = rate / (80.0 * cell)                         # whole cells per second
    return min((24, 25, 30), key=lambda f: abs(nominal - f))


def _drop_frame(frames, fps):
    """Drop-frame timecode is only defined for 30 fps; decide by majority vote."""
    return fps == 30 and sum(1 for f in frames if f[1]) * 2 > len(frames)


def decode(data):
    rate, channels, samples = read_wav(data)
    bits, cell = bits_from(samples)
    fps = _fps_from_cell(rate, cell)
    frames, reverse = _frames_of(bits, fps)
    if not frames:
        raise LtcError("no complete LTC frame")
    drop = _drop_frame(frames, fps)
    tcs = [f[0] for f in frames]
    sep = ";" if drop else ":"
    fmt = lambda t: "%02d:%02d:%02d%s%02d" % (t[0], t[1], t[2], sep, t[3])
    return {"sample_rate": rate, "channels": channels,
            "fps": "30000/1001" if drop else str(fps),
            "drop_frame": drop, "reverse": reverse, "frames": len(frames),
            "first": fmt(tcs[0]), "last": fmt(tcs[-1]), "user_bits": "%08X" % frames[0][2]}
