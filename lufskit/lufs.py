"""Programme loudness of WAV files per ITU-R BS.1770-4 / EBU R 128."""
import math
import struct
from array import array

# BS.1770-4 K-weighting coefficients at 48 kHz (tables 1 and 2).
SHELF_48K = (1.53512485958697, -2.69169618940638, 1.19839281085285,
             -1.69065929318241, 0.73248077421585)
RLB_48K = (1.0, -2.0, 1.0, -1.99004759761517, 0.99007228115877)

# Microsoft WAVE_FORMAT_EXTENSIBLE channel mask bits -> README speaker names.
MASK_BITS = (
    (0x00001, "FL"), (0x00002, "FR"), (0x00004, "FC"), (0x00008, "LFE"),
    (0x00010, "BL"), (0x00020, "BR"), (0x00040, "FLC"), (0x00080, "FRC"),
    (0x00100, "BC"), (0x00200, "SL"), (0x00400, "SR"), (0x00800, "TC"),
    (0x01000, "TFL"), (0x02000, "TFC"), (0x04000, "TFR"),
    (0x08000, "TBL"), (0x10000, "TBC"), (0x20000, "TBR"),
)

# Speaker layouts assumed when no channel mask is present.
DEFAULT_LAYOUTS = {
    1: ["FC"],
    2: ["FL", "FR"],
    6: ["FL", "FR", "FC", "LFE", "SL", "SR"],
}

# BS.1770-4 table 3: surround (back/side) channels carry a +1.5 dB weight.
SURROUND = frozenset(("BL", "BR", "BC", "SL", "SR"))
SURROUND_GAIN = 1.41

ABSOLUTE_GATE = -70.0          # LUFS, EBU R 128 / BS.1770-4 first gate
RELATIVE_GATE_OFFSET = -10.0   # LU below the absolute-gated loudness
LRA_GATE_OFFSET = -20.0        # LU below the mean (EBU Tech 3342)
LRA_FLOOR = -70.0


class LufsError(ValueError):
    pass


def kweighting_coeffs(rate):
    """Return the two K-weighting biquads (shelf, then RLB) for ``rate``.

    Coefficients follow the analogue prototype / bilinear-transform design in
    BS.1770-4 annex 2; at 48 kHz the published table values are used.
    """
    if rate <= 0:
        raise LufsError("invalid sample rate")
    if rate == 48000:
        return SHELF_48K, RLB_48K

    # Stage 1: pre-filter (high-shelf), BS.1770-4 table 1 / annex 2.1.
    f0, gain, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / rate)
    vh = 10.0 ** (gain / 20.0)
    vb = vh ** 0.4996667741545416
    a0 = 1.0 + k / q + k * k
    shelf = (
        (vh + vb * k / q + k * k) / a0,
        2.0 * (k * k - vh) / a0,
        (vh - vb * k / q + k * k) / a0,
        2.0 * (k * k - 1.0) / a0,
        (1.0 - k / q + k * k) / a0,
    )

    # Stage 2: RLB revised low-frequency-bending high-pass, annex 2.2.
    f0, q = 38.13547087602924, 0.5003270373238773
    k = math.tan(math.pi * f0 / rate)
    a0 = 1.0 + k / q + k * k
    rlb = (
        1.0 / a0, -2.0 / a0, 1.0 / a0,
        2.0 * (k * k - 1.0) / a0,
        (1.0 - k / q + k * k) / a0,
    )
    return shelf, rlb


def layout_for(channels, mask):
    """Resolve the speaker role of every channel, mask bits first."""
    if mask:
        names = [name for bit, name in MASK_BITS if mask & bit]
        if len(names) < channels:
            raise LufsError("channel mask does not cover every channel")
        return names[:channels]
    if channels in DEFAULT_LAYOUTS:
        return list(DEFAULT_LAYOUTS[channels])
    raise LufsError("no channel mask and no default layout for %d channels"
                    % channels)


def read_wav(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise LufsError("not a WAV file")
    p, fmt, body = 12, None, None
    while p + 8 <= len(data):
        cid, size = data[p:p + 4], struct.unpack_from("<I", data, p + 4)[0]
        chunk = data[p + 8:p + 8 + size]
        if cid == b"fmt " and len(chunk) >= 16:
            tag, ch, rate, _, _, bits = struct.unpack_from("<HHIIHH", chunk, 0)
            mask = 0
            if tag == 0xFFFE and len(chunk) >= 40:
                mask = struct.unpack_from("<I", chunk, 20)[0]
                tag = struct.unpack_from("<H", chunk, 24)[0]  # subtype tag
            fmt = (tag, ch, rate, bits, mask)
        elif cid == b"data" and body is None:
            body = chunk
        p += 8 + size + (size & 1)
    if fmt is None or body is None:
        raise LufsError("WAV file without fmt or data chunk")
    tag, ch, rate, bits, mask = fmt
    if ch < 1:
        raise LufsError("WAV file without channels")
    frame_bytes = bits // 8 * ch
    n = len(body) // frame_bytes
    body = body[:n * frame_bytes]

    if tag == 3 and bits == 32:
        flat = array("f")
        flat.frombytes(body)
    elif tag == 1 and bits == 16:
        flat = array("h")
        flat.frombytes(body)
        flat = array("f", (v / 32768.0 for v in flat))
    elif tag == 1 and bits == 24:
        # Expand each little-endian 3-byte sample to a signed 32-bit word.
        packed = bytearray(n * ch * 4)
        packed[0::4] = body[0::3]
        packed[1::4] = body[1::3]
        packed[2::4] = body[2::3]
        packed[3::4] = bytes(0xFF if b >= 0x80 else 0 for b in body[2::3])
        words = array("i")
        words.frombytes(packed)
        flat = array("f", (v / 8388608.0 for v in words))
    else:
        raise LufsError("unsupported sample format")
    return rate, [flat[c::ch] for c in range(ch)], layout_for(ch, mask)


def biquad(x, coeff):
    b0, b1, b2, a1, a2 = coeff
    y = array("f")
    x1 = x2 = y1 = y2 = 0.0
    append = y.append
    for v in x:
        out = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, v, y1, out
        append(out)
    return y


def prefix_sums(sq):
    acc, total = [0.0], 0.0
    for v in sq:
        total += v
        acc.append(total)
    return acc


def block_powers(acc, size, step):
    return [(acc[s + size] - acc[s]) / size
            for s in range(0, len(acc) - size, step)]


def loud(power):
    return -0.691 + 10.0 * math.log10(power) if power > 0.0 else -math.inf


def fmt(v):
    return None if v is None or v == -math.inf else "%.1f" % v


def parse(data):
    rate, chans, names = read_wav(data)
    shelf, rlb = kweighting_coeffs(rate)

    weighted = []
    for name, x in zip(names, chans):
        if name == "LFE":
            continue  # LFE never takes part in the measurement
        gain = SURROUND_GAIN if name in SURROUND else 1.0
        y = biquad(biquad(x, shelf), rlb)       # full K-weighting
        weighted.append((gain, prefix_sums(v * v for v in y)))

    peak = max((abs(v) for x in chans for v in x), default=0.0)
    n = len(chans[0])
    step = int(round(0.1 * rate))               # blocks start every 100 ms
    m_size, s_size = int(round(0.4 * rate)), int(round(3.0 * rate))

    def blocks(size):
        if not weighted or size > n:
            return []
        per_channel = [block_powers(acc, size, step) for _, acc in weighted]
        gains = [g for g, _ in weighted]
        return [sum(g * col[i] for g, col in zip(gains, per_channel))
                for i in range(len(per_channel[0]))]

    mom = blocks(m_size)                        # 400 ms, 100 ms hop
    short = blocks(s_size)                      # 3 s, 100 ms hop

    # Integrated loudness: absolute gate first, then the relative gate.
    abs_gated = [p for p in mom if loud(p) > ABSOLUTE_GATE]
    if abs_gated:
        relative = loud(sum(abs_gated) / len(abs_gated)) + RELATIVE_GATE_OFFSET
        gated = [p for p in abs_gated if loud(p) > relative]
        integrated = loud(sum(gated) / len(gated))
    else:
        relative, gated, integrated = -math.inf, [], None

    # Loudness range per EBU Tech 3342 on the 3 s short-term blocks.
    lra = None
    st_kept = [p for p in short if loud(p) > LRA_FLOOR]
    if st_kept:
        lra_threshold = loud(sum(st_kept) / len(st_kept)) + LRA_GATE_OFFSET
        values = sorted(loud(p) for p in st_kept if loud(p) > lra_threshold)
        if values:
            count = len(values)
            lra = (values[round((count - 1) * 0.95)]
                   - values[round((count - 1) * 0.10)])

    return {
        "sample_rate": rate,
        "channels": len(chans),
        "channel_names": names,
        "measured_channels": [name for name in names if name != "LFE"],
        "duration": "%.3f" % (n / rate),
        "sample_peak": fmt(20.0 * math.log10(peak)) if peak > 0.0 else None,
        "momentary_max": fmt(max((loud(p) for p in mom), default=None)),
        "short_term_max": fmt(max((loud(p) for p in short), default=None)),
        "integrated": fmt(integrated),
        "gated_blocks": len(gated),
        "loudness_range": fmt(lra),
    }
