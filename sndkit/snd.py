"""Sun/NeXT audio files (.au / .snd): header, annotation, encodings, peak level."""
import struct
from fractions import Fraction

HEADER_SIZE = 24
UNKNOWN = 0xFFFFFFFF
# encoding id -> (name, bits per sample); the ADPCM codes pack several samples into a byte
ENCODINGS = {1: ("mulaw", 8), 2: ("pcm8", 8), 3: ("pcm16", 16), 4: ("pcm24", 24), 5: ("pcm32", 32),
             6: ("float32", 32), 7: ("float64", 64), 23: ("g721", 4), 24: ("g722", 4),
             25: ("g723_3", 3), 26: ("g723_5", 5), 27: ("alaw", 8)}
ADPCM = {"g721", "g722", "g723_3", "g723_5"}


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


# largest sample magnitude used as full scale, and the magnitude counted as clipped
FULL_SCALE = {"mulaw": 32768, "alaw": 32768, "pcm8": 128, "pcm16": 32768,
              "pcm24": 1 << 23, "pcm32": 1 << 31}
CLIP_MAGNITUDE = {"mulaw": 32124, "alaw": 32256, "pcm8": 128, "pcm16": 32768,
                  "pcm24": 1 << 23, "pcm32": 1 << 31}


def _decoded_samples(name, data):
    """Yield decoded sample values for the encodings whose peak level can be measured."""
    if name == "mulaw":
        return (ulaw(b) for b in data)
    if name == "alaw":
        return (alaw(b) for b in data)
    if name == "pcm8":  # 8-bit linear PCM is unsigned with 128 at the midpoint
        return (b - 128 for b in data)
    if name == "pcm16":
        n = len(data) // 2
        return struct.unpack(">%dh" % n, data[:2 * n])
    if name == "pcm24":
        n = len(data) // 3
        return ((data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
                if data[i] < 0x80 else
                (data[i] << 16) | (data[i + 1] << 8) | data[i + 2] - (1 << 24)
                for i in range(0, 3 * n, 3))
    if name == "pcm32":
        n = len(data) // 4
        return struct.unpack(">%di" % n, data[:4 * n])
    if name == "float32":
        n = len(data) // 4
        return struct.unpack(">%df" % n, data[:4 * n])
    if name == "float64":
        n = len(data) // 8
        return struct.unpack(">%dd" % n, data[:8 * n])
    return None


def stats(name, data, channels):
    """Peak ratio per channel plus the count of full-scale (clipped) samples.

    Returns (overall_peak, [channel peaks ...], clipped_count); all peaks are
    `%.6f` strings and everything is None for the ADPCM encodings.
    """
    samples = _decoded_samples(name, data)
    if samples is None:
        return None, None, None
    top = [0] * channels
    clipped = 0
    floating = name in ("float32", "float64")
    clip_mag = CLIP_MAGNITUDE.get(name)
    for index, value in enumerate(samples):
        mag = abs(value)
        channel = index % channels
        if mag > top[channel]:
            top[channel] = mag
        if floating:
            if mag >= 1.0:
                clipped += 1
        elif mag >= clip_mag:
            clipped += 1
    if floating:
        scale = 1.0
    else:
        scale = FULL_SCALE[name]
    peak = "%.6f" % (max(top, default=0) / scale)
    return peak, ["%.6f" % (value / scale) for value in top], clipped


def peak(name, data):
    """Largest sample magnitude as a fraction of full scale, `%.6f`; None for encodings not decoded."""
    return stats(name, data, 1)[0]


def parse(data):
    if len(data) < HEADER_SIZE or data[:4] != b".snd":
        raise SndError("not a Sun/NeXT audio file")
    offset, size, enc, rate, channels = struct.unpack_from(">IIIII", data, 4)
    if offset < HEADER_SIZE or offset > len(data):
        raise SndError("bad data offset %d" % offset)
    if enc not in ENCODINGS:
        raise SndError("unsupported encoding %d" % enc)
    if channels < 1 or rate < 1:
        raise SndError("bad channel count or sample rate")
    name, bits = ENCODINGS[enc]
    size_known = size != UNKNOWN
    available = len(data) - offset
    if size_known:
        truncated = size > available
        length = min(size, available)
    else:  # streamed files: audio runs from the data offset to end of file
        truncated = False
        length = available
    # the annotation fills the space between the fixed header and the audio data
    note_text = data[HEADER_SIZE:offset].split(b"\x00", 1)[0].decode("iso-8859-1")
    note = note_text if note_text else None
    body = data[offset:offset + length]
    frame_bytes = bits * channels
    frames = length * 8 // frame_bytes
    whole = frames * frame_bytes // 8
    peak, channel_peaks, clipped = stats(name, body[:whole], channels)
    return {"encoding": name, "bits": bits, "sample_rate": rate, "channels": channels, "frames": frames,
            "duration": str(Fraction(frames, rate)), "annotation": note,
            "size_known": size_known, "truncated": truncated,
            "peak": peak, "channel_peaks": channel_peaks, "clipped": clipped}
