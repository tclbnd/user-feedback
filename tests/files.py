"""Builders for small synthetic CAF files used by the tests."""
import struct


def u32(n):
    return n.to_bytes(4, "big")


def chunk(kind, body, size=None):
    return kind + (len(body) if size is None else size).to_bytes(8, "big", signed=True) + body


def caf(*chunks_):
    return b"caff" + (1).to_bytes(2, "big") + bytes(2) + b"".join(chunks_)


def desc(rate, fid=b"lpcm", flags=0, bpp=4, fpp=1, ch=2, bits=16):
    return chunk(b"desc", struct.pack(">d4sIIIII", rate, fid, flags, bpp, fpp, ch, bits))


def data(audio, edit=0, size=None):
    return chunk(b"data", u32(edit) + audio, size)


def vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def pakt(sizes, priming=0, remainder=0, valid=None, fpp=1024, frames=None):
    """sizes: bytes of each packet (variable bytes); frames: per-packet frames when fpp is 0."""
    n = len(sizes)
    total = sum(frames) if frames else n * fpp
    if valid is None:
        valid = total - priming - remainder
    body = n.to_bytes(8, "big", signed=True) + valid.to_bytes(8, "big", signed=True)
    body += priming.to_bytes(4, "big", signed=True) + remainder.to_bytes(4, "big", signed=True)
    for i, s in enumerate(sizes):
        body += vlq(s)
        if frames:
            body += vlq(frames[i])
    return chunk(b"pakt", body)


def chan(tag, bitmap=0, descs=0):
    return chunk(b"chan", u32(tag) + u32(bitmap) + u32(descs) + bytes(20 * descs))


def info(pairs):
    body = u32(len(pairs))
    for k, v in pairs:
        body += k.encode() + b"\x00" + v.encode() + b"\x00"
    return chunk(b"info", body)


def pcm(frames, bits=16, little=False, flt=False):
    out = bytearray()
    for fr in frames:
        for v in fr:
            if flt:
                out += struct.pack(("<" if little else ">") + ("f" if bits == 32 else "d"), v)
            else:
                out += v.to_bytes(bits // 8, "little" if little else "big", signed=True)
    return bytes(out)
