"""Short test signals and a small WAV writer."""
import math
import struct

SPEAKER_FL = 0x00001
SPEAKER_FR = 0x00002
SPEAKER_FC = 0x00004
SPEAKER_LFE = 0x00008
SPEAKER_BL = 0x00010
SPEAKER_BR = 0x00020
SPEAKER_SL = 0x00200
SPEAKER_SR = 0x00400


def wav(channels, rate=48000, bits=16, float_samples=False, mask=None):
    """Build a WAV file from one float sample list per channel."""
    count = len(channels)
    frames = min(len(x) for x in channels) if channels else 0
    interleaved = [c[i] for i in range(frames) for c in channels]
    raw = _pcm(interleaved, bits, float_samples)
    block_align = (bits // 8) * count
    byte_rate = rate * block_align
    tag = 3 if float_samples else 1
    if mask is None:
        fmt = struct.pack("<HHIIHH", tag, count, rate, byte_rate, block_align, bits)
    else:
        fmt = (struct.pack("<HHIIHH", 0xFFFE, count, rate, byte_rate, block_align, bits)
               + struct.pack("<HHI", 22, bits, mask) + struct.pack("<H", tag) + bytes(14))
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(raw)) + raw)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _pcm(values, bits, float_samples):
    if float_samples:
        fmt = "<%dd" if bits == 64 else "<%df"
        return struct.pack(fmt % len(values), *values)
    if bits == 8:
        return bytes(max(0, min(255, int(round(v * 128)) + 128)) for v in values)
    if bits == 16:
        return struct.pack("<%dh" % len(values), *(_clip(v, 32768) for v in values))
    if bits == 24:
        return b"".join(_clip(v, 8388608).to_bytes(3, "little", signed=True) for v in values)
    if bits == 32:
        return struct.pack("<%di" % len(values), *(_clip(v, 2147483648) for v in values))
    raise ValueError("unsupported bit depth")


def _clip(value, full_scale):
    return max(-full_scale, min(full_scale - 1, int(round(value * full_scale))))


def sine(freq, seconds=2.0, amp=0.1, rate=48000, channels=1):
    """A steady sine, copied to ``channels`` channels."""
    n = int(round(seconds * rate))
    s = [amp * math.sin(2 * math.pi * freq * i / rate) for i in range(n)]
    return [list(s) for _ in range(channels)]


def programme(segments, rate=48000, channels=1):
    """Concatenate (dBFS, seconds) 1 kHz segments into one signal."""
    s = []
    for level, seconds in segments:
        amp = 10.0 ** (level / 20.0)
        s.extend(amp * math.sin(2 * math.pi * 1000 * i / rate)
                 for i in range(int(round(seconds * rate))))
    return [list(s) for _ in range(channels)]


def stereo_sine(seconds=2.0, amp=0.1, rate=48000):
    """A 997 Hz sine in both channels (kept for the existing tests)."""
    n = int(seconds * rate)
    s = [int(round(amp * 32768 * math.sin(2 * math.pi * 997 * i / rate))) for i in range(n)]
    raw = struct.pack("<%dh" % (2 * n), *[v for x in s for v in (x, x)])
    fmt = struct.pack("<HHIIHH", 1, 2, rate, rate * 4, 4, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(raw)) + raw
    return b"RIFF" + struct.pack("<I", len(body)) + body