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
    head_len_end = data.index(b"\n", 8)
    size = int(data[8:head_len_end].strip())
    text = data[head_len_end + 1:size]
    fields, p = {}, 0
    field_re = re.compile(rb"([A-Za-z0-9_]+)\s+-([irs])(\d*)\s")
    while p < len(text):
        m = field_re.match(text, p)
        if not m:
            if text[p:p + 8] == b"end_head":
                break
            q = text.find(b"\n", p)
            if q < 0:
                break
            p = q + 1
            continue
        name, typ, length_digits = m.group(1).decode("latin-1"), m.group(2), m.group(3)
        length = int(length_digits or 0)
        p = m.end()
        q = text.find(b"\n", p)
        line_end = q if q >= 0 else len(text)
        if typ == b"s":
            # -sN gives the exact value length, including spaces in the value.
            fields[name] = text[p:p + length].decode("latin-1")
            p += length
            q = text.find(b"\n", p)
            if q < 0:
                break
            p = q + 1
        else:
            raw = text[p:line_end].strip().decode("latin-1")
            fields[name] = int(raw) if typ == b"i" else raw
            if q < 0:
                break
            p = q + 1
    return size, fields


def _is_compressed(coding):
    coding = coding.lower()
    if "shorten" in coding or "embedded" in coding:
        return True
    compressed_names = {"shn", "flac", "gzip", "gz", "mp3", "mpeg", "opus", "vorbis", "speex"}
    return any(part.strip() in compressed_names for part in coding.split(","))


def _endian(order, width):
    if width == 1 or order == "1":
        return ""
    if order in ("10", "3210", "big", ">"):
        return ">"
    return "<"


def parse(data):
    size, f = header(data)
    channels = int(f.get("channel_count", 1))
    width = int(f.get("sample_n_bytes", 2))

    coding_text = str(f.get("sample_coding", "pcm"))
    coding_parts = [part.strip() for part in coding_text.split(",") if part.strip()]
    coding = coding_parts[0] if coding_parts else "pcm"
    compressed = _is_compressed(coding_text)

    default_order = {1: "1", 2: "01", 4: "0123"}.get(width, "01")
    order = str(f.get("sample_byte_format", default_order)).strip()
    byte_order = {"01": "little", "10": "big", "1": "single",
                  "0123": "little", "3210": "big"}.get(order, order)
    endian = _endian(order, width)

    body = data[size:]
    rate = f.get("sample_rate")
    count = f.get("sample_count")
    if count is None and not compressed:
        count = len(body) // (width * channels)

    duration = None
    if count is not None and rate:
        # sample_count is per channel, so do not divide by channel count.
        duration = str(Fraction(int(count), int(rate)))

    out = {"header_bytes": size, "fields": f, "channels": channels, "sample_rate": rate,
           "sample_count": count, "bytes_per_sample": width, "coding": coding,
           "compressed": compressed, "byte_order": byte_order, "duration": duration,
           "data_bytes": len(body), "complete": None, "checksum_ok": None, "peak": None}

    need = None
    raw = b""
    if count is not None:
        need = int(count) * channels * width
        raw = body[:need]
        raw = raw[:len(raw) // width * width]

        checksum = f.get("sample_checksum")
        if width == 2 and coding.lower() == "pcm" and checksum is not None:
            n = len(raw) // 2
            words = struct.unpack(endian + "%dH" % n, raw[:2 * n])
            out["checksum_ok"] = (sum(words) & 0xFFFF) == (int(checksum) & 0xFFFF)

    if compressed or count is None:
        return out

    out["complete"] = len(body) >= need

    coding_key = coding.lower()
    if coding_key in ("ulaw", "u-law", "mulaw", "mu-law"):
        encoded = struct.unpack("%dB" % len(raw), raw)
        vals = [ulaw(b) for b in encoded]
        full = 1 << 15
    elif coding_key in ("alaw", "a-law"):
        encoded = struct.unpack("%dB" % len(raw), raw)
        vals = [alaw(b) for b in encoded]
        full = 1 << 15
    elif width == 1:
        n = len(raw)
        vals = list(struct.unpack("%db" % n, raw))
        full = 1 << 7
    elif width in (2, 4):
        fmt = {2: "h", 4: "i"}[width]
        n = len(raw) // width
        vals = list(struct.unpack(endian + "%d%s" % (n, fmt), raw[:n * width]))
        full = 1 << (8 * width - 1)
    else:
        byteorder = "big" if endian == ">" else "little"
        vals = [int.from_bytes(raw[i:i + width], byteorder, signed=True)
                for i in range(0, len(raw), width)]
        full = 1 << (8 * width - 1)

    out["peak"] = "%.6f" % (max((abs(v) for v in vals), default=0) / full)
    return out
