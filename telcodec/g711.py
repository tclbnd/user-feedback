"""G.711 companding between 16-bit linear PCM and 8-bit codes (ITU-T G.711)."""

_ULAW_BIAS = 0x84
_ULAW_CLIP = 32635  # largest magnitude that stays on the mu-law code table
_ULAW_SEG_END = [0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF, 0x3FFF, 0x7FFF]
_ALAW_SEG_END = [0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF]


def _segment(value, ends):
    for i, end in enumerate(ends):
        if value <= end:
            return i
    return 8


def ulaw_encode(pcm):
    # Sign bit set for negative samples; the whole code is complemented.
    sign = 0x80 if pcm < 0 else 0
    mag = abs(pcm)
    if mag > _ULAW_CLIP:
        mag = _ULAW_CLIP
    mag += _ULAW_BIAS
    seg = _segment(mag, _ULAW_SEG_END)
    return ~(sign | (seg << 4) | ((mag >> (seg + 3)) & 0x0F)) & 0xFF


def ulaw_decode(code):
    code = ~code & 0xFF
    t = (((code & 0x0F) << 3) + _ULAW_BIAS) << ((code & 0x70) >> 4)
    return (_ULAW_BIAS - t) if code & 0x80 else (t - _ULAW_BIAS)


def alaw_encode(pcm):
    # A-law works on a 13-bit magnitude; even bits are toggled (0x55 mask,
    # combined with the sign convention this is 0xD5 for positive samples).
    pcm >>= 3
    if pcm >= 0:
        mask = 0xD5
    else:
        mask = 0x55
        pcm = -pcm - 1
    seg = _segment(pcm, _ALAW_SEG_END)
    if seg >= 8:  # input beyond the code table: saturate, never wrap
        return 0x7F ^ mask
    aval = seg << 4
    aval |= (pcm >> 1) & 0x0F if seg < 2 else (pcm >> seg) & 0x0F
    return aval ^ mask


def alaw_decode(code):
    code ^= 0x55
    t = (code & 0x0F) << 4
    seg = (code & 0x70) >> 4
    if seg == 0:
        t += 8
    elif seg == 1:
        t += 0x108
    else:
        t = (t + 0x108) << (seg - 1)
    return t if code & 0x80 else -t
