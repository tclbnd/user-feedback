"""Builders for small synthetic ASF headers used by the tests."""
import struct
import uuid

GUIDS = {
    "header": "75B22630-668E-11CF-A6D9-00AA0062CE6C", "data": "75B22636-668E-11CF-A6D9-00AA0062CE6C",
    "file": "8CABDCA1-A947-11CF-8EE4-00C00C205365", "stream": "B7DC0791-A9B7-11CF-8EE6-00C00C205365",
    "content": "75B22633-668E-11CF-A6D9-00AA0062CE6C", "ext_content": "D2D0A440-E307-11D2-97F0-00A0C95EA850",
    "extension": "5FBF03B5-A92E-11CF-8EE3-00C00C205365", "metadata": "C5F8CBEA-5BAF-4877-8467-AA8C44FA4CCA",
    "audio": "F8699E40-5B4D-11CF-A8FD-00805F5C442B", "video": "BC19EFC0-5B4D-11CF-A8FD-00805F5C442B",
    "no_ec": "20FB5700-5B55-11CF-A8FD-00805F5C442B", "spread": "BFC3CD50-618F-11CF-8BB2-00AA00B4E220",
    "padding": "1806D474-CADF-4509-A4BA-9AABCB96AAE8", "codec_list": "86D15240-311D-11D0-A3A4-00A0C90348F6",
    "ext_reserved": "ABD3D211-A9BA-11CF-8EE6-00C00C205365",
}


def g(name):
    return uuid.UUID(GUIDS[name]).bytes_le


def obj(name, body):
    return g(name) + struct.pack("<Q", len(body) + 24) + body


def u16(s, nul=True):
    return (s + ("\0" if nul else "")).encode("utf-16-le")


def file_props(play_s=60, preroll_ms=3000, packets=500, seekable=True, packet=3200, rate=128000):
    broadcast, maxp = False, None
    flags = (1 if broadcast else 0) | (2 if seekable else 0)
    play = 0 if broadcast else int(play_s * 10_000_000) + preroll_ms * 10_000
    return obj("file", bytes(16) + struct.pack("<QQQQQQIIII", 0, 0, 0 if broadcast else packets, play, play, preroll_ms,
                                               flags, packet, maxp or packet, rate))


def audio_stream(number=1, tag=0x0161, ch=2, rate=44100, bits=16, encrypted=False, ec=b""):
    wfx = struct.pack("<HHIIHHH", tag, ch, rate, rate * ch * 2, 4, bits, 10) + bytes(10)
    flags = number | (0x8000 if encrypted else 0)
    body = g("audio") + (g("spread") if ec else g("no_ec")) + struct.pack("<QIIHI", 0, len(wfx), len(ec), flags, 0) + wfx + ec
    return obj("stream", body)


def video_stream(number=2, w=1280, h=720, fourcc=b"WMV3", encrypted=False):
    bmih = struct.pack("<IiiHH", 40, w, h, 1, 24) + fourcc + bytes(20)
    ts = struct.pack("<IIBH", w, h, 2, len(bmih)) + bmih
    flags = number | (0x8000 if encrypted else 0)
    body = g("video") + g("no_ec") + struct.pack("<QIIHI", 0, len(ts), 0, flags, 0) + ts
    return obj("stream", body)


def content(title="", author="", copyright="", description="", rating=""):
    parts = [u16(x) if x else b"" for x in (title, author, copyright, description, rating)]
    return obj("content", struct.pack("<5H", *map(len, parts)) + b"".join(parts))


def ext_content(items):
    """items: list of (name, type, value) with value already encoded or a str/int."""
    body = struct.pack("<H", len(items))
    for name, vt, v in items:
        n = u16(name)
        raw = enc(vt, v, 4)
        body += struct.pack("<H", len(n)) + n + struct.pack("<HH", vt, len(raw)) + raw
    return obj("ext_content", body)


def enc(vt, v, bool_size):
    if isinstance(v, bytes):
        return v
    if vt == 0:
        return u16(v)
    if vt == 2:
        return int(v).to_bytes(bool_size, "little")
    if vt == 3:
        return v.to_bytes(4, "little")
    if vt == 4:
        return v.to_bytes(8, "little")
    if vt == 5:
        return v.to_bytes(2, "little")
    return bytes(v)


def padding(n=64):
    return obj("padding", bytes(n))


def asf(*objs, packets=0):
    inner = b"".join(objs)
    head = g("header") + struct.pack("<QIBB", 30 + len(inner), len(objs), 1, 2) + inner
    data = g("data") + struct.pack("<Q", 50) + bytes(16) + struct.pack("<QH", packets, 0x0101)
    return head + data
