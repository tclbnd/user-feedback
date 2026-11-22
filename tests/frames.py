"""Builders for small synthetic MPEG audio files used by the tests."""
RATES = {"1": (44100, 48000, 32000), "2": (22050, 24000, 16000),
         "2.5": (11025, 12000, 8000)}
KBPS = {"1": (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
        "2": (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
        "2.5": (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)}
VBITS = {"1": 3, "2": 2, "2.5": 0}
MODES = {"stereo": 0, "joint_stereo": 1, "dual_channel": 2, "mono": 3}


def frame(version="1", rate=44100, kbps=128, mode="joint_stereo", pad=0, seed=0, body=b""):
    """One Layer III frame; body is placed right after the 4-byte header."""
    b1 = 0xE0 | (VBITS[version] << 3) | (1 << 1) | 1
    b2 = (KBPS[version].index(kbps) << 4) | (RATES[version].index(rate) << 2) | (pad << 1)
    head = bytes([0xFF, b1, b2, MODES[mode] << 6])
    n = 144 * kbps * 1000 // rate + pad
    if version != "1":
        n = 72 * kbps * 1000 // rate + pad
    fill = bytes(((i * 7 + seed * 13) & 0x7F) for i in range(n))
    return (head + body + fill[4 + len(body):])[:n]


def stream(n, pads=(), **kw):
    return b"".join(frame(pad=1 if i in pads else 0, seed=i, **kw) for i in range(n))
