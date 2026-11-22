"""Builders for small synthetic Smacker files used by the tests."""
import struct


def rate_word(rate=22050, bits=16, channels=2, codec="smacker"):
    v = rate & 0xFFFFFF
    if channels == 2:
        v |= 1 << 28
    if bits == 16:
        v |= 1 << 29
    if codec == "bink-dct":
        v |= 1 << 30
    elif codec == "bink-rdft":
        v |= 2 << 30
    elif codec == "smacker":
        v |= 3 << 30
    return v


def smk(frames=10, width=320, height=200, rate=66, ring=False, tracks=(rate_word(),), version=b"SMK2"):
    flags, palette_every, key_every, audio_len, trees, track_mask, video = 0, 5, 5, None, 64, None, 120
    n = frames + (1 if ring else 0)
    rates = list(tracks) + [0] * (7 - len(tracks))
    mask = track_mask if track_mask is not None else [t for t, v in enumerate(rates) if v]
    bodies, sizes, types = [], [], []
    for i in range(n):
        typ, body = 0, b""
        if palette_every and i % palette_every == 0:
            typ |= 1
            body += bytes([12]) + bytes(51)                  # (12 + 1) * 4 = 52 bytes
        for t in mask:
            al = audio_len(i, t) if audio_len else 100 + 10 * t + i
            typ |= 2 << t
            body += struct.pack("<I", al + 4) + bytes(al)
        body += bytes(video + (i % 3))
        body += bytes(-len(body) % 4)
        size = len(body) | (1 if key_every and i % key_every == 0 else 0)
        bodies.append(body)
        sizes.append(size)
        types.append(typ)
    head = version + struct.pack("<IIIiI", width, height, frames, rate, flags | (1 if ring else 0))
    head += struct.pack("<7I", *[2000] * 7) + struct.pack("<I", trees) + struct.pack("<4I", 16, 16, 16, 16)
    head += struct.pack("<7I", *rates) + struct.pack("<I", 0)
    return head + struct.pack("<%dI" % n, *sizes) + bytes(types) + bytes(trees) + b"".join(bodies)
