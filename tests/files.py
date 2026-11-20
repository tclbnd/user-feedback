"""Builders for small synthetic AIFF / AIFF-C files used by the tests."""
import struct
from fractions import Fraction


def ext80(rate):
    rate = Fraction(rate)
    if rate == 0:
        return bytes(10)
    exp = rate.numerator.bit_length() - rate.denominator.bit_length()
    while rate / Fraction(2) ** exp >= 2:
        exp += 1
    while rate / Fraction(2) ** exp < 1:
        exp -= 1
    mant = round(rate / Fraction(2) ** exp * 2 ** 63)
    return (exp + 16383).to_bytes(2, "big") + mant.to_bytes(8, "big")


def pstr(text):
    b = bytes([len(text)]) + text.encode("latin-1")
    return b + (b"\x00" if len(b) % 2 else b"")


def chunk(cid, body):
    return cid + len(body).to_bytes(4, "big") + body + (b"\x00" if len(body) % 2 else b"")


def comm(ch, frames, bits, rate, comp=None, name=""):
    body = ch.to_bytes(2, "big") + frames.to_bytes(4, "big") + bits.to_bytes(2, "big") + ext80(rate)
    if comp is not None:
        body += comp.encode("latin-1") + pstr(name)
    return chunk(b"COMM", body)


def ssnd(data, offset=0, lead=b""):
    return chunk(b"SSND", offset.to_bytes(4, "big") + bytes(4) + (lead + bytes(offset))[:offset] + data)


def mark(items):
    body = len(items).to_bytes(2, "big")
    for mid, pos, name in items:
        body += mid.to_bytes(2, "big") + pos.to_bytes(4, "big") + pstr(name)
    return chunk(b"MARK", body)


def form(chunks, aifc=False):
    body = (b"AIFC" + chunk(b"FVER", (0xA2805140).to_bytes(4, "big"))) if aifc else b"AIFF"
    body += b"".join(chunks)
    return b"FORM" + len(body).to_bytes(4, "big") + body


def pcm(frames, bits, little=False, kind="int"):
    """frames: list of tuples of sample values (ints, or floats for kind='float')."""
    out = bytearray()
    width = (bits + 7) // 8
    for fr in frames:
        for v in fr:
            if kind == "float":
                out += struct.pack("<f" if little else ">f", v)
                continue
            v = v << (width * 8 - bits)
            out += v.to_bytes(width, "little" if little else "big", signed=True)
    return bytes(out)
