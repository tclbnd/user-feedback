"""Matroska builder."""
import struct


def vsize(n, width=None, unknown=False):
    if width is None:
        width = 1
        while n >= (1 << (7 * width)) - 1:
            width += 1
    if unknown:
        n = (1 << (7 * width)) - 1
    return ((1 << (7 * width)) | n).to_bytes(width, "big")


def eid(i):
    return i.to_bytes((i.bit_length() + 7) // 8, "big")


def el(i, payload=b"", width=None, unknown=False):
    return eid(i) + vsize(len(payload), width, unknown) + payload


def u(i, v):
    return el(i, v.to_bytes(max(1, (v.bit_length() + 7) // 8), "big"))


def track(number, kind=1, codec="V_VP9"):
    return el(0xAE, u(0xD7, number) + u(0x73C5, number * 1000 + 7) + u(0x83, kind) + el(0x86, codec.encode()))


def info(scale=1000000, duration=None, double=False):
    body = u(0x2AD7B1, scale)
    if duration is not None:
        body += el(0x4489, struct.pack(">d" if double else ">f", duration))
    return el(0x1549A966, body + el(0x4D80, b"mkvbuild"))


def frame_bytes(n, seed=0):
    return bytes(((i * 29 + seed) & 0xFF) for i in range(n))


def lace(frames, mode):
    if mode == "none":
        return 0, frames[0]
    head = bytes([len(frames) - 1])
    if mode == "xiph":
        for f in frames[:-1]:
            n = len(f)
            head += b"\xff" * (n // 255) + bytes([n % 255])
        return 1, head + b"".join(frames)
    if mode == "fixed":
        return 2, head + b"".join(frames)
    head += vsize(len(frames[0]))
    for a, b in zip(frames, frames[1:-1]):
        d = len(b) - len(a)
        for w in range(1, 9):
            bias = (1 << (7 * w - 1)) - 1
            if -bias <= d <= bias:
                head += ((1 << (7 * w)) | (d + bias)).to_bytes(w, "big")
                break
    return 3, head + b"".join(frames)


def simple(track_no, rel, frames, key=True, mode="none"):
    bits, body = lace(frames, mode)
    return el(0xA3, vsize(track_no) + struct.pack(">h", rel) + bytes([(0x80 if key else 0) | (bits << 1)]) + body)


def group(track_no, rel, frames, ref=None, mode="none", duration=None):
    bits, body = lace(frames, mode)
    blk = el(0xA1, vsize(track_no) + struct.pack(">h", rel) + bytes([bits << 1]) + body)
    extra = b""
    if duration is not None:
        extra += u(0x9B, duration)
    if ref is not None:
        extra += el(0xFB, struct.pack(">h", ref))
    return el(0xA0, blk + extra)


def cluster(ts, blocks, unknown=False, void=False, crc=False):
    body = b""
    if crc:
        body += el(0xBF, b"\x12\x34\x56\x78")
    body += u(0xE7, ts)
    if void:
        body += el(0xEC, bytes(9))
    body += b"".join(blocks)
    if unknown:
        return eid(0x1F43B675) + b"\x01\xff\xff\xff\xff\xff\xff\xff" + body
    return el(0x1F43B675, body)


def mkv(tracks, clusters, scale=1000000, duration=None, double=False, seg_unknown=False):
    header = el(0x1A45DFA3, u(0x4286, 1) + u(0x42F7, 1) + u(0x42F2, 4) + u(0x42F3, 8) + el(0x4282, b"matroska") + u(0x4287, 4) + u(0x4285, 2))
    body = info(scale, duration, double) + el(0x1654AE6B, b"".join(tracks)) + b"".join(clusters)
    if seg_unknown:
        return header + eid(0x18538067) + b"\x01\xff\xff\xff\xff\xff\xff\xff" + body
    return header + el(0x18538067, body, width=8)
