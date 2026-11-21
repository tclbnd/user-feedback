"""QOA (Quite OK Audio) files: header, frames, LMS-predicted 3-bit slices (qoaformat.org)."""
import struct
import zlib
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction

SLICE_LEN = 20
SLICES_PER_FRAME = 256
FRAME_LEN = SLICES_PER_FRAME * SLICE_LEN  # 5120 samples per channel, max in one frame
MAX_CHANNELS = 255
FILE_HEADER = 8
FRAME_HEADER = 8
LMS_STATE_BYTES = 16

# qoa_scalefactor_tab[s] = round((s + 1) ** 2.75), from the reference decoder.
SCALEFACTOR = (1, 7, 21, 45, 84, 138, 211, 304,
               421, 562, 731, 928, 1157, 1419, 1715, 2048)

# Quantized residual values (before division by 4) for the 8 codes.
_QUANT = (3, -3, 10, -10, 18, -18, 28, -28)


def _round_half_away(value):
    """C round() semantics: .5 rounds away from zero, for both signs."""
    return int(Decimal(value).quantize(Decimal(1), rounding=ROUND_HALF_UP))


# qoa_dequant_tab[scalefactor][quantized] =
# round(scalefactor[s] * quant[q] / 4), bit identical to the reference table.
DEQUANT = tuple(
    tuple(_round_half_away(Decimal(SCALEFACTOR[s]) * q / 4) for q in _QUANT)
    for s in range(16)
)


class QoaError(ValueError):
    pass


def _clamp16(v):
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


def _s16(v):
    return v - 65536 if v & 0x8000 else v


def _frame_size(channels, slices):
    return FRAME_HEADER + LMS_STATE_BYTES * channels + 8 * slices * channels


def decode_frame(data, pos):
    """Decode one QOA frame starting at pos.

    Returns (frame_dict, new_pos).  frame_dict carries the channel count,
    sample rate, the per-channel sample count from the frame header and the
    decoded samples as a list of per-channel lists.
    """
    if pos + FRAME_HEADER > len(data):
        raise QoaError("truncated frame header")
    ch = data[pos]
    if ch == 0 or ch > MAX_CHANNELS:
        raise QoaError("invalid channel count")
    # The sample rate is a 24-bit big-endian integer at frame offset 1..3.
    rate = (data[pos + 1] << 16) | (data[pos + 2] << 8) | data[pos + 3]
    if rate == 0:
        raise QoaError("invalid sample rate")
    # Frame header: num_channels u8, sample_rate u24, frame_samples u16,
    # frame_size u16 (all big-endian) -- samples precede the byte size.
    fsamples, fsize = struct.unpack_from(">HH", data, pos + 4)
    if fsamples == 0 or fsamples > FRAME_LEN:
        raise QoaError("invalid frame sample count")
    if fsize == 0 or pos + fsize > len(data):
        raise QoaError("truncated or invalid frame")
    slices = (fsamples + SLICE_LEN - 1) // SLICE_LEN
    if fsize != _frame_size(ch, slices):
        raise QoaError("frame size does not match its samples")

    p = pos + FRAME_HEADER
    hist, weights = [], []
    for c in range(ch):
        hist.append([_s16(int.from_bytes(data[p + 2 * i:p + 2 * i + 2], "big")) for i in range(4)])
        weights.append([_s16(int.from_bytes(data[p + 8 + 2 * i:p + 10 + 2 * i], "big")) for i in range(4)])
        p += LMS_STATE_BYTES

    out = [[] for _ in range(ch)]
    # Slice words are interleaved per slice position: for every slice, one
    # 8-byte word per channel (channel 0 first, then channel 1, ...).
    for si in range(slices):
        for c in range(ch):
            word = int.from_bytes(data[p:p + 8], "big")
            p += 8
            sf = word >> 60
            h, w = hist[c], weights[c]
            first_sample = si * SLICE_LEN
            for k in range(SLICE_LEN):
                # The last slice may hold fewer than 20 real samples; the
                # remaining quantized bits inside the word are padding.
                if first_sample + k >= fsamples:
                    break
                q = (word >> (57 - 3 * k)) & 7
                pred = (w[0] * h[0] + w[1] * h[1] + w[2] * h[2] + w[3] * h[3]) >> 13
                r = DEQUANT[sf][q]
                s = _clamp16(pred + r)
                delta = r >> 4
                for i in range(4):
                    w[i] += -delta if h[i] < 0 else delta
                h.pop(0)
                h.append(s)
                out[c].append(s)

    return {"channels": ch, "rate": rate, "samples": fsamples, "pcm": out}, pos + fsize


def parse(data):
    if len(data) < FILE_HEADER + FRAME_HEADER or data[:4] != b"qoaf":
        raise QoaError("not a QOA file")
    total, = struct.unpack_from(">I", data, 4)
    streaming = total == 0

    pos, frames, decoded, peak, first = FILE_HEADER, 0, 0, 0, None
    crc = 0
    while pos < len(data):
        f, pos = decode_frame(data, pos)
        if first is None:
            first = f
        elif not streaming and (f["channels"], f["rate"]) != (first["channels"], first["rate"]):
            raise QoaError("channels or sample rate change in a non-streaming file")
        frames += 1
        decoded += f["samples"]
        inter = bytearray()
        for k in range(f["samples"]):
            for c in range(f["channels"]):
                v = f["pcm"][c][k]
                if abs(v) > peak:
                    peak = abs(v)
                inter += struct.pack("<h", v)
        crc = zlib.crc32(inter, crc)

    if first is None:
        raise QoaError("no frames")

    # Streaming files carry 0 in the file header; the sample count is the
    # sum of the per-frame sample counts.
    samples = decoded if streaming else total
    return {"streaming": streaming, "channels": first["channels"], "sample_rate": first["rate"],
            "samples": samples, "frames": frames, "decoded_samples": decoded,
            "duration": str(Fraction(samples, first["rate"])), "peak": peak,
            "pcm_crc32": "%08x" % (crc & 0xFFFFFFFF)}
