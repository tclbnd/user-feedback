"""Builders for small synthetic FLIC files used by the tests."""
import struct


def sub(stype, body):
    return struct.pack("<IH", len(body) + 6, stype) + body


def color256(colors, start=0):
    zero_count = False
    n = len(colors)
    body = struct.pack("<HBB", 1, start, 0 if (zero_count or n == 256) else n)
    return sub(4, body + b"".join(bytes(c) for c in colors))


def brun(size=200):
    return sub(15, bytes(size))


def delta(flc=True, size=60):
    return sub(7 if flc else 12, bytes(size))


def frame(subs=()):
    delay = 0
    body = b"".join(subs)
    return struct.pack("<IHHHHHHH", len(body) + 16, 0xF1FA, len(subs), delay, 0, 0, 0, 0)[:16] + body


def flic(fmt="FLC", frames_=(), speed=70, width=320, height=200):
    count, ring, pre, oframe = None, True, b"", True
    count = len(frames_) if count is None else count
    magic = 0xAF12 if fmt == "FLC" else 0xAF11
    body = b"".join(frames_)
    if ring and frames_:
        body += frame([delta(fmt == "FLC")])
    head = bytearray(128)
    struct.pack_into("<IHHHHHH", head, 0, 0, magic, count, width, height, 8, 3)
    if fmt == "FLC":
        struct.pack_into("<I", head, 16, speed)
        if oframe:
            struct.pack_into("<II", head, 80, 128 + len(pre), 128 + len(pre) + len(frames_[0]) if frames_ else 0)
    else:
        struct.pack_into("<H", head, 16, speed)
    out = bytes(head) + pre + body
    return struct.pack("<I", len(out)) + out[4:]


def anim(fmt="FLC", n=10, speed=70, pal=None, **kw):
    pal = pal or [(i, 255 - i, (i * 7) % 256) for i in range(256)]
    first = frame([color256(pal), brun()])
    rest = [frame([delta(fmt == "FLC")]) for _ in range(n - 1)]
    return flic(fmt, [first] + rest, speed, **kw)
