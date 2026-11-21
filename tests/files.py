"""Builders for small synthetic MP4 files used by the tests."""
import struct


def box(kind, body=b""):
    if isinstance(kind, str):
        kind = kind.encode("latin-1")
    return struct.pack(">I4s", len(body) + 8, kind) + body


def full(kind, version, body, flags=0):
    return box(kind, bytes([version]) + flags.to_bytes(3, "big") + body)


def mvhd(duration, timescale=1000, version=0):
    if version:
        body = struct.pack(">QQIQ", 0, 0, timescale, duration)
    else:
        body = struct.pack(">IIII", 0, 0, timescale, duration)
    return full("mvhd", version, body + bytes(80))


def data(value, dtype=1):
    if isinstance(value, str):
        value = value.encode("utf-16-be" if dtype == 2 else "utf-8")
    return box("data", struct.pack(">I", dtype) + bytes(4) + value)


def item(kind, *datas):
    return box(kind, b"".join(datas))


def text(kind, s):
    return item(kind, data(s))


def number(kind, n, width, signed=True):
    return item(kind, data(n.to_bytes(width, "big", signed=signed), 21 if signed else 22))


def pair(kind, n, total):
    tail = b"\0\0" if kind == "trkn" else b""
    return item(kind, data(struct.pack(">HHH", 0, n, total) + tail, 0))


def hdlr():
    return full("hdlr", 0, bytes(4) + b"mdirappl" + bytes(9))


def meta(*items):
    return full("meta", 0, hdlr() + box("ilst", b"".join(items)))


def chpl(chapters):
    body = bytes(4) + bytes([len(chapters)])
    for start, title in chapters:
        t = title.encode()
        body += struct.pack(">QB", start, len(t)) + t
    return full("chpl", 1, body)


def udta(*kids):
    return box("udta", b"".join(kids))


def mp4(*moov_kids, mdat=1000):
    return box("ftyp", b"M4A \0\0\0\0M4A mp42isom") + box("mdat", bytes(mdat)) + box("moov", b"".join(moov_kids))
