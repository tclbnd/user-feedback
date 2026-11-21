"""Builders for small synthetic Creative Voice Files used by the tests."""
import struct


def voc(blocks_, version=0x010A, header_size=26, bad_check=False):
    check = ((~version + 0x1234) & 0xFFFF) ^ (1 if bad_check else 0)
    head = b"Creative Voice File\x1a" + struct.pack("<HHH", header_size, version, check)
    head += bytes(header_size - len(head))
    return head + b"".join(blocks_) + b"\x00"


def block(kind, body):
    return bytes([kind]) + len(body).to_bytes(3, "little") + body


def sound(tc, data, pack=0):
    return block(1, bytes([tc, pack]) + data)


def cont(data):
    return block(2, data)


def silence(samples, tc):
    return block(3, struct.pack("<HB", samples - 1, tc))


def marker(n):
    return block(4, struct.pack("<H", n))


def text(s):
    return block(5, s.encode("latin-1") + b"\0")


def extended(tc, pack=0, stereo=False):
    return block(8, struct.pack("<HBB", tc, pack, 1 if stereo else 0))


def newfmt(rate, bits, channels, codec, data):
    return block(9, struct.pack("<IBBH", rate, bits, channels, codec) + bytes(4) + data)


def pcm8(values):
    return bytes(v + 128 for v in values)


def pcm16(values):
    return b"".join(struct.pack("<h", v) for v in values)
