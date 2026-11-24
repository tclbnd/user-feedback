"""WebP files built byte by byte for the tests."""
import struct


def chunk(cid, payload):
    """A RIFF chunk, padded to an even size as the spec requires."""
    return cid + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) & 1 else b"")


def riff(*parts):
    body = b"WEBP" + b"".join(parts)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def vp8(w=64, h=48):
    return bytes([0x10, 0x02, 0x00]) + b"\x9d\x01\x2a" + struct.pack("<HH", w, h) + bytes(40)


def vp8l(w=64, h=48, alpha=False):
    bits = (w - 1) | (h - 1) << 14 | (1 if alpha else 0) << 28
    return b"\x2f" + struct.pack("<I", bits) + bytes(40)


def u24(v):
    return struct.pack("<I", v)[:3]


def still(w=64, h=48):
    return riff(chunk(b"VP8 ", vp8(w, h)))


def extended(w, h, flags, *parts):
    """VP8X chunk plus whatever follows it."""
    return chunk(b"VP8X", bytes([flags, 0, 0, 0]) + u24(w - 1) + u24(h - 1))


def anim(bg=(0, 0, 0, 0), loops=0):
    # bg is (blue, green, red, alpha) as stored.
    return chunk(b"ANIM", bytes(bg) + struct.pack("<H", loops))


def frame(x, y, w, h, duration=100, blend=False, dispose=False, *parts):
    """One ANMF chunk. x, y, w, h are on-canvas pixel values."""
    flags = (0x02 if not blend else 0x00) | (0x01 if dispose else 0x00)
    hdr = u24(x // 2) + u24(y // 2) + u24(w - 1) + u24(h - 1) + u24(duration) + bytes([flags])
    return chunk(b"ANMF", hdr + b"".join(parts))


def animated(canvas=(320, 240), features=0x02, bg=(0, 0, 0, 0), loops=0, frames=(), extra=()):
    parts = [extended(canvas[0], canvas[1], features)]
    if features & 0x20:
        parts.append(chunk(b"ICCP", b"fake icc profile"))  # odd payload: needs a pad byte
    parts.append(anim(bg, loops))
    parts.extend(frames)
    parts.extend(extra)
    return riff(*parts)
