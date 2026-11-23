"""Programme loudness of WAV files per ITU-R BS.1770-4 / EBU R 128.

The measurement follows BS.1770-4:

* every channel is K-weighting filtered (high-shelf pre-filter, then the
  RLB high-pass), the coefficients being derived for the actual sample rate;
* channel power is summed with the BS.1770 weights (front channels 1.0,
  surround channels 1.41) and the LFE channel is left out;
* loudness is -0.691 + 10 log10(power);
* the integrated loudness uses 400 ms momentary blocks with a -70 LUFS
  absolute gate followed by a -10 LU relative gate;
* the loudness range uses 3 s short-term blocks with a -70 LUFS absolute gate
  and a -20 LU relative gate (EBU Tech 3342).

Blocks start every 100 ms.
"""
import math
import struct
import sys
from array import array
from collections import namedtuple

# Analog prototype of the K-weighting stages (BS.1770-4, table 1).  The
# coefficients for a given sample rate are derived from these.
SHELF_F0 = 1681.974450955533
SHELF_GAIN = 3.999843853973347
SHELF_Q = 0.7071752369554196
SHELF_VB = 0.4996667741545416
HIGHPASS_F0 = 38.13547087602444
HIGHPASS_Q = 0.5003270373238773

# Published 48 kHz coefficients (BS.1770-4, table 1).
SHELF_48K = (1.53512485958697, -2.69169618940638, 1.19839281085285,
             -1.69065929318241, 0.73248077421585)
HIGHPASS_48K = (1.0, -2.0, 1.0, -1.99004745483398, 0.99007225036621)

LUFS_OFFSET = -0.691
GATE_ABSOLUTE = -70.0        # LUFS, integrated and loudness range
GATE_RELATIVE = -10.0        # LU, integrated
LRA_GATE_RELATIVE = -20.0    # LU, loudness range

MOMENTARY_SECONDS = 0.4
SHORT_TERM_SECONDS = 3.0
BLOCK_STEP_SECONDS = 0.1

FRONT_WEIGHT = 1.0
SURROUND_WEIGHT = 1.41

# WAVE_FORMAT_EXTENSIBLE speaker positions, in mask bit order.
POSITIONS = (
    ("FL", 0x00001), ("FR", 0x00002), ("FC", 0x00004), ("LFE", 0x00008),
    ("BL", 0x00010), ("BR", 0x00020), ("FLC", 0x00040), ("FRC", 0x00080),
    ("BC", 0x00100), ("SL", 0x00200), ("SR", 0x00400), ("TC", 0x00800),
    ("TFL", 0x01000), ("TFC", 0x02000), ("TFR", 0x04000), ("TBL", 0x08000),
    ("TBC", 0x10000), ("TBR", 0x20000),
)
FRONT = frozenset(("FL", "FR", "FC", "FLC", "FRC", "TFL", "TFC", "TFR"))
LFE = frozenset(("LFE",))
POSITION_BITS = {bit: name for name, bit in POSITIONS}

# Channel order used when the file carries no usable channel mask.
DEFAULT_LAYOUTS = {
    1: ("FC",),
    2: ("FL", "FR"),
    3: ("FL", "FR", "FC"),
    4: ("FL", "FR", "BL", "BR"),
    5: ("FL", "FR", "FC", "BL", "BR"),
    6: ("FL", "FR", "FC", "LFE", "BL", "BR"),
    7: ("FL", "FR", "FC", "LFE", "BC", "SL", "SR"),
    8: ("FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"),
}

Wav = namedtuple("Wav", "sample_rate scale channels samples mask")


class LufsError(ValueError):
    pass


# ---------------------------------------------------------------------------
# WAV reading


def read_wav(data):
    """Return (sample rate, channel samples) as floats in [-1, 1]."""
    wav = read_wav_info(data)
    return wav.sample_rate, channels_of(wav)


def channels_of(wav):
    """One float sample list per channel of a :class:`Wav`."""
    return [[v * wav.scale for v in wav.samples[c::wav.channels]]
            for c in range(wav.channels)]


def read_wav_info(data):
    """Return a :class:`Wav` holding the header fields and interleaved samples."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise LufsError("not a WAV file")
    pos, fmt, parts = 12, None, []
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        start, stop = pos + 8, pos + 8 + size
        truncated = stop > len(data)
        if chunk_id == b"fmt ":
            fmt = _parse_fmt(data[start:min(stop, len(data))])
        elif chunk_id == b"data":
            parts.append(data[start:len(data) if truncated else stop])
        if truncated:
            break
        pos = stop + (size & 1)
    if fmt is None or not parts:
        raise LufsError("WAV file without fmt or data chunk")
    tag, channels, rate, bits, container, mask = fmt
    body = b"".join(parts)
    frames = len(body) // (container * channels)
    body = body[:frames * container * channels]
    flat, scale = _decode(body, tag, bits, container)
    return Wav(rate, scale, channels, flat, mask)


def _parse_fmt(chunk):
    if len(chunk) < 16:
        raise LufsError("truncated fmt chunk")
    tag, channels, rate, _, block_align, bits = struct.unpack_from("<HHIIHH", chunk)
    mask = 0
    if tag == 0xFFFE:
        if len(chunk) < 40:
            raise LufsError("truncated WAVE_FORMAT_EXTENSIBLE header")
        mask = struct.unpack_from("<I", chunk, 20)[0]
        tag = struct.unpack_from("<H", chunk, 24)[0]
    if channels <= 0 or rate <= 0 or bits <= 0:
        raise LufsError("invalid WAV header")
    if block_align and block_align % channels == 0:
        container = block_align // channels
    else:
        container = (bits + 7) // 8
    # Trust the container when a header declares more valid bits than fit.
    bits = min(bits, container * 8)
    return tag, channels, rate, bits, container, mask


def _decode(body, tag, bits, container):
    """Interleaved samples as a compact array plus the scale to full scale.

    Integer PCM is returned as integers; multiplying a sample by ``scale``
    gives the value in [-1, 1).  Float files are returned unscaled.
    """
    count = len(body) // container * container
    data = body[:count]
    whole = 1 << (bits - 1)
    if tag == 3:
        if container not in (4, 8):
            raise LufsError("unsupported float sample size")
        values = array("f" if container == 4 else "d")
        values.frombytes(data)
        if sys.byteorder != "little":
            values.byteswap()
        return values, 1.0
    if tag != 1 or container not in (1, 2, 3, 4):
        raise LufsError("unsupported sample format")
    if container == 1:
        # 8-bit PCM is unsigned; translate into signed bytes.
        table = bytes((v + 128) & 0xFF for v in range(256))
        return array("b", data.translate(table)), 1.0 / whole
    if container == 3:
        # No 24-bit array type: place the three bytes in the high bytes of a
        # 32-bit word so the sign bit lands where the integer type expects it.
        # WAV keeps samples left-justified, so full scale is 2**31 whatever
        # the declared bit depth.
        words = bytearray(count // 3 * 4)
        first = 1 if sys.byteorder == "little" else 0
        words[first::4] = data[0::3]
        words[first + 1::4] = data[1::3]
        words[first + 2::4] = data[2::3]
        return array("i", bytes(words)), 1.0 / float(1 << 31)
    values = array({2: "h", 4: "i"}[container])
    values.frombytes(data)
    if sys.byteorder != "little":
        values.byteswap()
    # Samples narrower than their container are left-justified.
    shift = container * 8 - bits
    if shift:
        values = array(values.typecode, (v >> shift for v in values))
    return values, 1.0 / whole


# ---------------------------------------------------------------------------
# K-weighting


def shelf_coefficients(rate):
    """High-shelf pre-filter of BS.1770-4 for an arbitrary sample rate."""
    k = math.tan(math.pi * SHELF_F0 / rate)
    vh = 10.0 ** (SHELF_GAIN / 20.0)
    vb = vh ** SHELF_VB
    a0 = 1.0 + k / SHELF_Q + k * k
    return ((vh + vb * k / SHELF_Q + k * k) / a0,
            2.0 * (k * k - vh) / a0,
            (vh - vb * k / SHELF_Q + k * k) / a0,
            2.0 * (k * k - 1.0) / a0,
            (1.0 - k / SHELF_Q + k * k) / a0)


def highpass_coefficients(rate):
    """RLB high-pass of BS.1770-4 for an arbitrary sample rate."""
    k = math.tan(math.pi * HIGHPASS_F0 / rate)
    a0 = 1.0 + k / HIGHPASS_Q + k * k
    return (1.0, -2.0, 1.0,
            2.0 * (k * k - 1.0) / a0,
            (1.0 - k / HIGHPASS_Q + k * k) / a0)


def biquad(x, c):
    b0, b1, b2, a1, a2 = c
    y, x1, x2, y1, y2 = [], 0.0, 0.0, 0.0, 0.0
    for v in x:
        out = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, v, y1, out
        y.append(out)
    return y


def k_weight(x, rate):
    """K-weight one channel (both stages)."""
    return biquad(biquad(x, shelf_coefficients(rate)), highpass_coefficients(rate))


def segment_energies(x, scale, shelf, hp, bounds):
    """K-weight one channel; return the energy in each interval of ``bounds``.

    ``bounds`` holds the sample indices of the 100 ms grid, so the segments
    follow the clock exactly even when the rate is not a multiple of 10 Hz.
    """
    sb0, sb1, sb2, sa1, sa2 = shelf
    hb0, hb1, hb2, ha1, ha2 = hp
    out = []
    sx1 = sx2 = sy1 = sy2 = 0.0
    hx1 = hx2 = hy1 = hy2 = 0.0
    acc = 0.0
    nxt = 1
    stop = bounds[nxt]
    for i, v in enumerate(x):
        v *= scale
        y = sb0 * v + sb1 * sx1 + sb2 * sx2 - sa1 * sy1 - sa2 * sy2
        sx2, sx1, sy2, sy1 = sx1, v, sy1, y
        z = hb0 * y + hb1 * hx1 + hb2 * hx2 - ha1 * hy1 - ha2 * hy2
        hx2, hx1, hy2, hy1 = hx1, y, hy1, z
        acc += z * z
        if i + 1 == stop:
            out.append(acc)
            acc = 0.0
            nxt += 1
            if nxt >= len(bounds):
                break
            stop = bounds[nxt]
    return out


# ---------------------------------------------------------------------------
# Channel roles and blocks


def channel_roles(channels, mask):
    """Channel roles, from the WAVE_FORMAT_EXTENSIBLE mask when it is usable.

    The mask lists the speakers present in bit order, which is also the order
    of the channels in the file.  Bits without a BS.1770 role (height and
    other positions) are measured as surround channels.
    """
    if mask:
        roles = []
        for bit in range(32):
            value = 1 << bit
            if mask & value:
                roles.append(POSITION_BITS.get(value, "SUR"))
        if len(roles) == channels:
            return tuple(roles)
    layout = DEFAULT_LAYOUTS.get(channels)
    if layout is not None:
        return layout
    # Wider layouts: the first channels of the standard WAVE speaker order.
    return tuple(name for name, _ in POSITIONS[:channels])


def channel_weights(channels, mask):
    weights = []
    for role in channel_roles(channels, mask):
        if role in LFE:
            weights.append(0.0)
        elif role in FRONT:
            weights.append(FRONT_WEIGHT)
        else:
            weights.append(SURROUND_WEIGHT)
    return weights


def block_powers(segments, hops, bounds):
    """Power of every block of ``hops`` consecutive segments, stepping one segment."""
    if hops <= 0 or len(segments) < hops:
        return []
    acc = [0.0]
    for v in segments:
        acc.append(acc[-1] + v)
    return [(acc[i + hops] - acc[i]) / (bounds[i + hops] - bounds[i])
            for i in range(len(segments) - hops + 1)]


def loud(power):
    return LUFS_OFFSET + 10 * math.log10(power) if power > 0 else -math.inf


def fmt(v):
    return None if v is None or v == -math.inf else "%.1f" % v


def gate(powers, relative_lu):
    """Absolute gate at -70 LUFS, then a relative gate below the mean."""
    above = [p for p in powers if p > 10.0 ** ((GATE_ABSOLUTE - LUFS_OFFSET) / 10.0)]
    if not above:
        return []
    threshold = sum(above) / len(above) * 10.0 ** (relative_lu / 10.0)
    return [p for p in above if p > threshold]


def integrated_loudness(momentary):
    """Gated integrated loudness and the number of blocks that pass both gates."""
    passing = gate(momentary, GATE_RELATIVE)
    if not passing:
        return None, 0
    return loud(sum(passing) / len(passing)), len(passing)


def loudness_range(short_term):
    """Loudness range in LU (EBU Tech 3342).

    The 10th and 95th percentiles are read at ``round((n - 1) * p)`` of the
    sorted short-term loudness values, as described in the README.
    """
    passing = gate(short_term, LRA_GATE_RELATIVE)
    if not passing:
        return None
    values = sorted(loud(p) for p in passing)
    n = len(values)
    return values[round((n - 1) * 0.95)] - values[round((n - 1) * 0.10)]


# ---------------------------------------------------------------------------
# Measurement


def parse(data):
    wav = read_wav_info(data)
    rate, scale, channels, samples, mask = wav
    if channels <= 0 or not samples:
        raise LufsError("WAV file without samples")
    frames = len(samples) // channels
    weights = channel_weights(channels, mask)
    peak = max((abs(v) for v in samples), default=0) * scale

    momentary_hops = int(round(MOMENTARY_SECONDS / BLOCK_STEP_SECONDS))
    short_term_hops = int(round(SHORT_TERM_SECONDS / BLOCK_STEP_SECONDS))
    # Sample index of every 100 ms boundary inside the file.  Rounding each
    # boundary independently keeps the grid on the clock at any sample rate.
    count = int(frames / (BLOCK_STEP_SECONDS * rate))
    bounds = [int(round(i * BLOCK_STEP_SECONDS * rate)) for i in range(count + 1)]
    while len(bounds) > 1 and bounds[-1] > frames:
        bounds.pop()

    # Energy of every 100 ms segment, summed over the measured channels with
    # their BS.1770 weights.  Blocks then cover a whole number of segments.
    segments = [0.0] * (len(bounds) - 1)
    if segments:
        shelf, hp = shelf_coefficients(rate), highpass_coefficients(rate)
        for c, weight in enumerate(weights):
            if not weight:
                continue
            # De-interleave one channel at a time to keep memory low.
            x = samples[c::channels][:bounds[-1]]
            for i, energy in enumerate(segment_energies(x, scale, shelf, hp, bounds)):
                segments[i] += weight * energy

    momentary = block_powers(segments, momentary_hops, bounds)
    short_term = block_powers(segments, short_term_hops, bounds)
    integrated, gated_blocks = integrated_loudness(momentary)

    return {
        "sample_rate": rate,
        "channels": channels,
        "measured_channels": sum(1 for w in weights if w),
        "duration": "%.3f" % (frames / rate),
        "sample_peak": fmt(20 * math.log10(peak)) if peak > 0 else None,
        "momentary_max": fmt(max((loud(p) for p in momentary), default=None)),
        "short_term_max": fmt(max((loud(p) for p in short_term), default=None)),
        "integrated": fmt(integrated),
        "gated_blocks": gated_blocks,
        "loudness_range": fmt(loudness_range(short_term)),
    }

