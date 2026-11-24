"""Small NSV streams for the tests."""
import struct


def header(vlen, alen, naux=0):
    word = vlen << 4 | naux
    return bytes([word & 0xFF, word >> 8 & 0xFF, word >> 16 & 0xFF]) + struct.pack("<H", alen)


def sync_frame(vlen=100, alen=20, rate=25, aux=()):
    head = b"NSVs" + b"VP62" + b"MP3 " + struct.pack("<HHBH", 320, 240, rate, 0)
    chunks = b"".join(struct.pack("<H", size) + kind + bytes(size) for kind, size in aux)
    return head + header(vlen, alen, len(aux)) + chunks + bytes(vlen - len(chunks) + alen)


def plain_frame(vlen=100, alen=20, aux=()):
    chunks = b"".join(struct.pack("<H", size) + kind + bytes(size) for kind, size in aux)
    return b"\xEF\xBE" + header(vlen, alen, len(aux)) + chunks + bytes(vlen - len(chunks) + alen)


def file_header(metadata="", length_ms=3003):
    metas = metadata.encode("latin-1") + b"\0"
    start = 28 + len(metas) + 12
    return b"NSVf" + struct.pack("<6I", start, 999, length_ms, len(metas), 3, 3) + metas + bytes(12)
