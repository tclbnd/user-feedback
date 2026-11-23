"""Sun/NeXT audio files (.au / .snd): header, annotation, encodings, peak level."""
import struct
from fractions import Fraction

UNKNOWN = 0xFFFFFFFF
ENCODINGS = {1: ("mulaw", 8), 2: ("pcm8", 8), 3: ("pcm16", 16), 4: ("pcm24", 24), 5: ("pcm32", 32),
             6: ("float32", 32), 7: ("float64", 64), 23: ("g721", 8), 24: ("g722", 8), 25: ("g723_3", 8),
             26: ("g723_5", 8), 27: ("alaw", 8)}


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


def peak(name, data):
    """Largest sample magnitude as a fraction of full scale, `%.6f`; None for encodings not decoded."""
    if name == "mulaw":
        top = max((abs(ulaw(b)) for b in data), default=0) / 32768
    elif name == "alaw":
        top = max((abs(alaw(b)) for b in data), default=0) / 32768
    elif name == "pcm8":
        top = max((abs(v) for v in struct.unpack(">%dB" % len(data), data)), default=0) / 128
    elif name == "pcm16":
        n = len(data) // 2
        top = max((abs(v) for v in struct.unpack("<%dh" % n, data[:2 * n])), default=0) / 32768
    else:
        return None
    return "%.6f" % top


def parse(data):
    if len(data) < 24 or data[:4] != b".snd":
        raise SndError("not a Sun/NeXT audio file")
    offset, size, enc, rate, channels = struct.unpack_from(">IIIII", data, 4)
    if offset < 24 or offset > len(data):
        raise SndError("bad data offset %d" % offset)
    if enc not in ENCODINGS:
        raise SndError("unsupported encoding %d" % enc)
    if channels < 1 or rate < 1:
        raise SndError("bad channel count or sample rate")
    name, bits = ENCODINGS[enc]
    available = len(data) - offset
    length = min(size, available) if size != UNKNOWN else size
    body = data[24:24 + length]
    frames = length * 8 // (bits * channels)
    whole = frames * bits * channels // 8
    note = None
    return {"encoding": name, "bits": bits, "sample_rate": rate, "channels": channels, "frames": frames,
            "duration": str(Fraction(frames, rate)), "annotation": note,
            "size_known": size != UNKNOWN, "truncated": False,
            "peak": peak(name, body[:whole])}
