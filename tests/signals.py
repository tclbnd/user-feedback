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
