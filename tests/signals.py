"""Short test signals."""
import math
import struct


def stereo_sine(seconds=2.0, amp=0.1, rate=48000):
    n = int(seconds * rate)
    s = [int(round(amp * 32768 * math.sin(2 * math.pi * 997 * i / rate))) for i in range(n)]
    raw = struct.pack("<%dh" % (2 * n), *[v for x in s for v in (x, x)])
    fmt = struct.pack("<HHIIHH", 1, 2, rate, rate * 4, 4, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(raw)) + raw
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _pack_pcm(channels, rate, bits, mask=0):
    ch, n = len(channels), len(channels[0])

    def clamp(v):
        return max(-1.0, min(1.0, v))

    if bits == 16:
        vals = [int(round(clamp(channels[c][i]) * 32767))
                for i in range(n) for c in range(ch)]
        raw = struct.pack("<%dh" % (ch * n), *vals)
        tag = 1
    elif bits == 24:
        raw = bytearray()
        for i in range(n):
            for c in range(ch):
                v = int(round(clamp(channels[c][i]) * 8388607))
                raw += bytes((v & 255, (v >> 8) & 255, (v >> 16) & 255))
        raw = bytes(raw)
        tag = 1
    elif bits == 32:
        vals = [channels[c][i] for i in range(n) for c in range(ch)]
        raw = struct.pack("<%df" % (ch * n), *vals)
        bits, tag = 32, 3
    else:
        raise ValueError("unsupported bits")
    if mask:
        head = struct.pack("<HHIIHH", 0xFFFE, ch, rate,
                           rate * ch * bits // 8, ch * bits // 8, bits)
        ext = struct.pack("<HHI", 22, bits, mask)
        guid = struct.pack("<H", tag) + b"\x00" * 14
        fmt = head + ext + guid
    else:
        fmt = struct.pack("<HHIIHH", tag, ch, rate,
                          rate * ch * bits // 8, ch * bits // 8, bits)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data"
            + struct.pack("<I", len(raw)) + raw)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def sine_wav(channels, seconds, amp=0.1, freq=997.0, rate=48000,
             bits=16, mask=0):
    n = int(seconds * rate)
    chans = [[amp * math.sin(2 * math.pi * freq * i / rate) for i in range(n)]
             for _ in range(channels)]
    return _pack_pcm(chans, rate, bits, mask)


def channels_wav(chans, rate=48000, bits=16, mask=0):
    return _pack_pcm(chans, rate, bits, mask)


def segments_wav(parts, rate=48000, bits=16, channels=1, mask=0):
    """parts: list of (seconds, amplitude) 1 kHz sine segments."""
    chans = [[] for _ in range(channels)]
    for seconds, amp in parts:
        n = int(seconds * rate)
        for c in range(channels):
            chans[c].extend(amp * math.sin(2 * math.pi * 1000 * i / rate)
                            for i in range(n))
    return _pack_pcm(chans, rate, bits, mask)
