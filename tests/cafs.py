"""Builders for small synthetic CAF files used by the tests."""
import struct


def chunk(ctype, body, size=None):
    return ctype + struct.pack(">q", len(body) if size is None else size) + body


def desc(rate=44100.0, fmt=b"lpcm", flags=0, bpp=4, fpp=1, ch=2, bits=16):
    return chunk(b"desc", struct.pack(">d4sIIIII", rate, fmt, flags, bpp, fpp, ch, bits))


def varint(v):
    out = [v & 0x7F]
    v >>= 7
    while v:
        out.insert(0, 0x80 | (v & 0x7F))
        v >>= 7
    return bytes(out)


def pakt(sizes=(), frames=(), valid=0, priming=0, remainder=0, packets=None):
    n = packets if packets is not None else max(len(sizes), len(frames))
    body = struct.pack(">qqii", n, valid, priming, remainder)
    for i in range(max(len(sizes), len(frames))):
        if sizes:
            body += varint(sizes[i])
        if frames:
            body += varint(frames[i])
    return chunk(b"pakt", body)


def data(audio, edit=0):
    return chunk(b"data", struct.pack(">I", edit) + audio)


def info(pairs):
    body = struct.pack(">I", len(pairs)) + b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in pairs)
    return chunk(b"info", body)


def chan(tag=(101 << 16) | 2, bitmap=0):
    return chunk(b"chan", struct.pack(">III", tag, bitmap, 0))


def audio(n, seed=0):
    return bytes(((i * 13 + seed) & 0xFF) for i in range(n))


def caf(*chunks_):
    return b"caff" + struct.pack(">HH", 1, 0) + b"".join(chunks_)
