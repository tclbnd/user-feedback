"""NIST SPHERE speech files: ASCII header (typed fields), sample format, byte order, coding, checksum."""
import re
import struct
from fractions import Fraction


class SphereError(ValueError):
    pass


def ulaw(b):
    b = ~b & 0xFF
    sign, exp, mant = b & 0x80, (b >> 4) & 7, b & 0x0F
    mag = (((mant << 3) + 0x84) << exp) - 0x84
    return -mag if sign else mag


def alaw(b):
    b ^= 0x55
    sign, exp, mant = b & 0x80, (b >> 4) & 7, b & 0x0F
    mag = (mant << 4) + 8 if exp == 0 else ((mant << 4) + 0x108) << (exp - 1)
    return mag if sign else -mag


def header(data):
    if data[:8] != b"NIST_1A\n":
        raise SphereError("not a NIST SPHERE file")
    nl = data.index(b"\n", 8)
    size = 1024
    text = data[nl + 1:size]
    fields, p = {}, 0
    while p < len(text):
        m = re.compile(rb"([A-Za-z0-9_]+)\s+-([irs])(\d*)\s").match(text, p)
        if not m:
            if text[p:p + 8] == b"end_head":
                break
            q = text.find(b"\n", p)
            if q < 0:
                break
            p = q + 1
            continue
        name, typ, n = m.group(1).decode(), m.group(2), m.group(3)
        p = m.end()
        if typ == b"s":
            q = text.find(b"\n", p)
            value = text[p:q].split()[0].decode("latin-1") if text[p:q].split() else ""
            p = q
        else:
            q = text.find(b"\n", p)
            raw = text[p:q].strip().decode()
            value = int(raw) if typ == b"i" else raw
            p = q
        fields[name] = value
        q = text.find(b"\n", p)
        p = q + 1 if q >= 0 else len(text)
    return size, fields


def parse(data):
    size, f = header(data)
    channels = f.get("channel_count", 1)
    width = f.get("sample_n_bytes", 2)
    coding = str(f.get("sample_coding", "pcm")).split(",")
    order = str(f.get("sample_byte_format", "01" if width == 2 else "1"))
    compressed = False
    body = data[size:]
    rate = f.get("sample_rate")
    count = f.get("sample_count")
    if count is None and not compressed:
        count = len(body) // (width * channels)
    out = {"header_bytes": size, "fields": f, "channels": channels, "sample_rate": rate, "sample_count": count,
           "bytes_per_sample": width, "coding": coding[0], "compressed": compressed,
           "byte_order": {"01": "little", "10": "big", "1": "single", "0123": "little", "3210": "big"}.get(order, order),
           "duration": str(Fraction(count, rate * channels)) if count is not None and rate else None,
           "data_bytes": len(body), "complete": None, "checksum_ok": None, "peak": None}
    if compressed:
        return out
    need = count * channels * width
    out["complete"] = len(body) >= need
    raw = body[:need]
    n = len(raw) // width
    if width == 1:
        vals, full = list(struct.unpack("%db" % n, raw)), 128
    else:
        e = "<"
        fmt = "h"
        vals, full = list(struct.unpack(e + "%d%s" % (n, fmt), raw[:n * width])), 1 << (8 * width - 1)
    out["peak"] = "%.6f" % (max((abs(v) for v in vals), default=0) / full)
    return out
