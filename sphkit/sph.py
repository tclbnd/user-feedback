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


_FIELD = re.compile(rb"[ \t]*([A-Za-z0-9_]+) +-([irs])(\d*) +")


def header(data):
    if data[:8] != b"NIST_1A\n":
        raise SphereError("not a NIST SPHERE file")
    nl = data.find(b"\n", 8)
    if nl < 0:
        raise SphereError("truncated header")
    try:
        size = int(data[8:nl])
    except ValueError:
        raise SphereError("bad header size %r" % data[8:nl])
    if size < nl + 1:
        raise SphereError("header size %d too small" % size)
    text = data[nl + 1:size]
    fields, p = {}, 0
    while p < len(text):
        m = _FIELD.match(text, p)
        if not m:
            if text.startswith(b"end_head", p):
                break
            q = text.find(b"\n", p)
            if q < 0:
                break
            p = q + 1
            continue
        name, typ, width = m.group(1).decode(), m.group(2), int(m.group(3) or 0)
        p = m.end()
        q = text.find(b"\n", p)
        end = len(text) if q < 0 else q
        if end > p and text[end - 1:end] == b"\r":
            end -= 1
        if typ == b"s":
            # A -sN value is exactly N characters, spaces included; a shorter
            # value ends at the line break.
            stop = p + width if width and p + width <= end else end
            value = text[p:stop].decode("latin-1")
            p = stop
        else:
            raw = text[p:end].decode("latin-1").strip()
            value = int(raw.split()[0]) if typ == b"i" else raw
            p = end
        fields[name] = value
        if q < 0:
            break
        p = q + 1
    return size, fields


def _samples(raw, width, endian, codec):
    """Decode data bytes into integer sample values."""
    if codec == "ulaw":
        return [ulaw(b) for b in raw]
    if codec == "alaw":
        return [alaw(b) for b in raw]
    if width == 1:
        return list(struct.unpack("%db" % len(raw), raw))
    code = {2: "h", 4: "i"}.get(width)
    if code is None:
        return []
    n = len(raw) // width
    return list(struct.unpack("%s%d%s" % (endian, n, code), raw[:n * width]))


_ORDER = {"01": "little", "0123": "little", "10": "big", "3210": "big", "1": "single"}
_RAW = ("pcm", "ulaw", "alaw")


def parse(data):
    size, f = header(data)
    channels = f.get("channel_count", 1)
    width = f.get("sample_n_bytes", 2)
    parts = [c.strip() for c in str(f.get("sample_coding", "pcm")).split(",")]
    parts = [c for c in parts if c] or ["pcm"]
    coding = parts[0]
    codec = coding.lower()
    compressed = len(parts) > 1 or codec not in _RAW
    order = str(f.get("sample_byte_format", "1" if width == 1 else "01")).strip()
    byte_order = _ORDER.get(order, order)
    endian = {"little": "<", "big": ">"}.get(byte_order, "<")
    # µ-law and A-law are one byte per sample whatever the declared width.
    step = 1 if codec in ("ulaw", "alaw") else width
    body = data[size:]
    rate = f.get("sample_rate")
    count = f.get("sample_count")
    if count is None and not compressed:
        count = len(body) // (step * channels)
    out = {"header_bytes": size, "fields": f, "channels": channels, "sample_rate": rate,
           "sample_count": count, "bytes_per_sample": width, "coding": coding,
           "compressed": compressed, "byte_order": byte_order,
           "duration": str(Fraction(count, rate)) if count is not None and rate else None,
           "data_bytes": len(body), "complete": None, "checksum_ok": None, "peak": None}
    if compressed:
        return out
    need = count * channels * step
    out["complete"] = len(body) >= need
    vals = _samples(body[:need], width, endian, codec)
    if codec in ("ulaw", "alaw"):
        full = 32768
    elif width == 1:
        full = 128
    else:
        full = 1 << (8 * width - 1)
    out["peak"] = "%.6f" % (max((abs(v) for v in vals), default=0) / full)
    if "sample_checksum" in f and width == 2 and codec == "pcm":
        out["checksum_ok"] = sum(v & 0xFFFF for v in vals) % 65536 == f["sample_checksum"]
    return out
