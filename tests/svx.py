"""Builders for small synthetic 8SVX files used by the tests."""
import struct

DELTAS = (-34, -21, -13, -8, -5, -3, -2, -1, 0, 1, 2, 3, 5, 8, 13, 21)


def chunk(cid, body):
    return cid + len(body).to_bytes(4, "big") + body + (b"\0" if len(body) & 1 else b"")


def vhdr(one, rep, rate, octaves=1, comp=0, volume=0x10000, cycle=0):
    return chunk(b"VHDR", struct.pack(">IIIHBBI", one, rep, cycle, rate, octaves, comp, volume))


def form(*chunks_):
    body = b"8SVX" + b"".join(chunks_)
    return b"FORM" + len(body).to_bytes(4, "big") + body


def chan(flags):
    return chunk(b"CHAN", flags.to_bytes(4, "big"))


def text(cid, s):
    return chunk(cid, s.encode("latin-1"))


def raw(values):
    return bytes(v & 0xFF for v in values)


def fib_pack(initial, codes):
    """codes: nibble codes (0..15), two per byte, high first."""
    if len(codes) % 2:
        codes = list(codes) + [8]
    out = bytearray([0, initial & 0xFF])
    for i in range(0, len(codes), 2):
        out.append((codes[i] << 4) | codes[i + 1])
    return bytes(out)
