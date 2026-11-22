"""Builders for small synthetic APNG files used by the tests."""
import struct
import zlib

SIG = b"\x89PNG\r\n\x1a\n"


def chunk(ctype, body):
    bad_crc = False
    crc = zlib.crc32(ctype.encode() + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + ctype.encode() + body + struct.pack(">I", crc ^ (1 if bad_crc else 0))


def ihdr(w=320, h=240):
    return chunk("IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))


def actl(n, plays=0):
    return chunk("acTL", struct.pack(">II", n, plays))


def fctl(seq, w=320, h=240, x=0, y=0, num=1, den=10, dispose=0, blend=0):
    return chunk("fcTL", struct.pack(">IIIIIHHBB", seq, w, h, x, y, num, den, dispose, blend))


def idat(n=64):
    return chunk("IDAT", zlib.compress(bytes(n)))


def fdat(seq, n=64):
    return chunk("fdAT", struct.pack(">I", seq) + zlib.compress(bytes(n)))


def apng(frames=4, w=320, h=240, plays=0, delays=None, disposes=None):
    default_in, extra = True, b""
    """Sequence numbers follow the spec: fcTL and fdAT share one counter starting at 0."""
    delays = delays or [(1, 10)] * frames
    disposes = disposes or [0] * frames
    out = SIG + ihdr(w, h) + actl(frames, plays) + extra
    seq = 0
    if not default_in:
        out += idat()
    for i in range(frames):
        num, den = delays[i]
        out += fctl(seq, w, h, num=num, den=den, dispose=disposes[i])
        seq += 1
        if i == 0 and default_in:
            out += idat()
        else:
            out += fdat(seq)
            seq += 1
    return out + chunk("IEND", b"")
