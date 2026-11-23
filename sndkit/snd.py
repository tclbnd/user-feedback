"""Sun/NeXT audio files (.au / .snd): header, annotation, encodings, peak level."""
import math
import struct
from fractions import Fraction

UNKNOWN = 0xFFFFFFFF
HEADER = 24
# encoding code -> (name, bits per sample); ADPCM codes use their true sample width.
ENCODINGS = {1: ("mulaw", 8), 2: ("pcm8", 8), 3: ("pcm16", 16), 4: ("pcm24", 24), 5: ("pcm32", 32),
             6: ("float32", 32), 7: ("float64", 64), 23: ("g721", 4), 24: ("g722", 4), 25: ("g723_3", 3),
             26: ("g723_5", 5), 27: ("alaw", 8)}
# Magnitude of the most extreme sample for each linear/PCM encoding.
FULL_SCALE = {"pcm8": 128, "pcm16": 32768, "pcm24": 8388608, "pcm32": 2147483648,
              "mulaw": 32768, "alaw": 32768, "float32": 1.0, "float64": 1.0}
# Largest-magnitude code word that counts as clipped.
CLIP_LEVEL = {"mulaw": 32124, "alaw": 32256}


class SndError(ValueError):
    pass


def ulaw(b):
    """G.711 mu-law byte to 16-bit linear."""
    b = ~b & 0xFF
    sign, exp, mant = b & 0x80, (b >> 4) & 7, b & 0x0F
    mag = ((mant << 3) + 0x84) << exp
    mag -= 0x84
    return -mag if sign else mag


def alaw(b):
    """G.711 A-law byte to 16-bit linear."""
    b ^= 0x55
    sign, exp, mant = b & 0x80, (b >> 4) & 7, b & 0x0F
    if exp == 0:
        mag = (mant << 4) + 8
    else:
        mag = ((mant << 4) + 0x108) << (exp - 1)
    return mag if sign else -mag


def decode(name, data):
    """Decode whole sample values (big-endian, signed where applicable); None for the ADPCM encodings."""
    if name == "mulaw":
        return [ulaw(b) for b in data]
    if name == "alaw":
        return [alaw(b) for b in data]
    if name == "pcm8":  # 8-bit linear PCM is signed two's complement
        return list(struct.unpack(">%db" % len(data), data))
    if name == "pcm16":
        n = len(data) // 2
        return list(struct.unpack(">%dh" % n, data[:2 * n]))
    if name == "pcm24":
        return [int.from_bytes(data[i:i + 3], "big", signed=True) for i in range(0, len(data) - 2, 3)]
    if name == "pcm32":
        n = len(data) // 4
        return list(struct.unpack(">%di" % n, data[:4 * n]))
    if name == "float32":
        n = len(data) // 4
        return list(struct.unpack(">%df" % n, data[:4 * n]))
    if name == "float64":
        n = len(data) // 8
        return list(struct.unpack(">%dd" % n, data[:8 * n]))
    return None  # g721 / g722 / g723_3 / g723_5 are not decoded here


def _clipped(name, value):
    if name in CLIP_LEVEL:
        return abs(value) >= CLIP_LEVEL[name]
    if name in ("float32", "float64"):
        return not math.isnan(value) and abs(value) >= 1.0
    return abs(value) >= FULL_SCALE[name]  # most negative / most positive integer of the sample size


def levels(name, data, channels):
    """Overall and per-channel peak (fraction of full scale, `%.6f`) plus clipping; None for ADPCM."""
    samples = decode(name, data)
    if samples is None:
        return None, None, None
    scale = FULL_SCALE[name]
    peak = 0
    chan = [0] * channels
    clipped = False
    for index, value in enumerate(samples):
        if math.isnan(value):  # NaN has no usable level; infinities still count and clip
            continue
        magnitude = abs(value)
        if magnitude > peak:
            peak = magnitude
        c = index % channels
        if magnitude > chan[c]:
            chan[c] = magnitude
        if not clipped and _clipped(name, value):
            clipped = True
    return "%.6f" % (peak / scale), ["%.6f" % (v / scale) for v in chan], clipped


def peak(name, data):
    """Largest sample magnitude as a fraction of full scale, `%.6f`; None for encodings not decoded."""
    top, _, _ = levels(name, data, 1)
    return top


def parse(data):
    if len(data) < HEADER or data[:4] != b".snd":
        raise SndError("not a Sun/NeXT audio file")
    offset, size, enc, rate, channels = struct.unpack_from(">IIIII", data, 4)
    if offset < HEADER or offset > len(data):
        raise SndError("bad data offset %d" % offset)
    if enc not in ENCODINGS:
        raise SndError("unsupported encoding %d" % enc)
    if channels < 1 or rate < 1:
        raise SndError("bad channel count or sample rate")
    name, bits = ENCODINGS[enc]

    # The annotation is the NUL-terminated text between the header and the audio data.
    note = data[HEADER:offset].split(b"\x00", 1)[0].decode("utf-8", "replace") or None

    size_known = size != UNKNOWN
    available = len(data) - offset  # audio data runs from the header's offset to end of file
    if size_known:
        length = min(size, available)
        truncated = available < size
    else:
        length = available  # streamed files: data length is unknown, take everything to EOF
        truncated = False

    frames = length * 8 // (bits * channels)
    whole = frames * bits * channels // 8  # bytes holding the whole sample frames
    body = data[offset:offset + whole]
    top, chan, clipped = levels(name, body, channels)
    return {"encoding": name, "bits": bits, "sample_rate": rate, "channels": channels, "frames": frames,
            "duration": str(Fraction(frames, rate)), "annotation": note,
            "size_known": size_known, "truncated": truncated,
            "peak": top, "channel_peaks": chan, "clipped": clipped}
