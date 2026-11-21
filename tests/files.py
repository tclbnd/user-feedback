"""Builders for small synthetic QOA files used by the tests."""
import struct


def _rng(seed):
    x = seed * 2654435761 % (1 << 32) or 1
    while True:
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        yield x


def frame(ch=2, rate=44100, fsamples=5120, seed=1, sf_max=6):
    history, weights = (0, 0, 0, 0), (0, 0, 0, 1 << 12)
    slices = -(-fsamples // 20)
    out = bytes([ch]) + rate.to_bytes(3, "big") + struct.pack(">HH", fsamples, 8 + 16 * ch + 8 * slices * ch)
    for c in range(ch):
        out += b"".join(struct.pack(">h", h) for h in history) + b"".join(struct.pack(">h", w) for w in weights)
    r = _rng(seed)
    for _ in range(slices * ch):
        v = next(r)
        sf = v % (sf_max + 1)
        word = sf << 60
        for k in range(20):
            word |= (next(r) & 7) << (57 - 3 * k)
        out += word.to_bytes(8, "big")
    return out


def qoa(frames_, samples=None):
    """frames_: list of frame() bytes; samples None = sum of their samples, 0 = streaming file."""
    if samples is None:
        samples = sum(struct.unpack_from(">H", f, 4)[0] for f in frames_)
    return b"qoaf" + struct.pack(">I", samples) + b"".join(frames_)


def song(total, ch=2, rate=44100, seed=1, **kw):
    streaming = False
    fr, left, k = [], total, 0
    while left > 0:
        n = min(5120, left)
        fr.append(frame(ch, rate, n, seed + k, **kw))
        left -= n
        k += 1
    return qoa(fr, 0 if streaming else total)
