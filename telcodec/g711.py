"""G.711 companding between 16-bit linear PCM and 8-bit codes."""

BIAS = 0x84
_SEG_END = [0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF, 0x3FFF, 0x7FFF]


def _segment(value):
    for i, end in enumerate(_SEG_END):
        if value <= end:
            return i
    return 8


def ulaw_encode(pcm):
    sign = 0x80 if pcm < 0 else 0
    mag = min(abs(pcm), 32767) + BIAS
    seg = _segment(mag)
    return sign | (seg << 4) | ((mag >> (seg + 3)) & 0x0F)


def ulaw_decode(code):
    code = ~code & 0xFF
    t = (((code & 0x0F) << 3) + BIAS) << ((code & 0x70) >> 4)
    return (BIAS - t) if code & 0x80 else (t - BIAS)


def alaw_encode(pcm):
    sign = 0x80 if pcm < 0 else 0
    mag = min(abs(pcm), 32767) >> 3
    seg = _segment(mag << 3) if mag >= 32 else 0
    mant = (mag >> 1) & 0x0F if seg < 2 else (mag >> seg) & 0x0F
    return sign | (seg << 4) | mant


def alaw_decode(code):
    t = (code & 0x0F) << 4
    seg = (code & 0x70) >> 4
    if seg == 0:
        t += 8
    else:
        t = (t + 0x108) << (seg - 1)
    return -t if code & 0x80 else t
