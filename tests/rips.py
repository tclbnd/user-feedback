"""Builders for small synthetic NSF and NSFe files used by the tests."""
import struct


def pad(s, n=32):
    b = s.encode("latin-1")
    return b[:n] + bytes(max(0, n - len(b)))


def nsf(songs=12, start=1, title="Stage Theme", artist="Composer", copyright="1987 Company", region=0, chips=0,
        ntsc=16639, pal=19997, prg=4096):
    banks, version, appended, load = bytes(8), 1, b"", 0x8000
    h = b"NESM\x1a" + struct.pack("<BBBHHH", version, songs, start, load, 0x8003, 0x8006)
    h += pad(title) + pad(artist) + pad(copyright) + struct.pack("<H", ntsc) + banks + struct.pack("<H", pal)
    h += bytes([region, chips, 0]) + ((prg if version >= 2 and appended else 0).to_bytes(3, "little"))
    return h + bytes(prg) + appended


def chunk(cid, body):
    return struct.pack("<I", len(body)) + cid.encode() + body


def info(load=0x8000, region=0, chips=0, songs=1, start=0):
    body = struct.pack("<HHHBB", load, 0x8003, 0x8006, region, chips)
    if songs is not None:
        body += bytes([songs])
        if start is not None:
            body += bytes([start])
    return chunk("INFO", body)


def nsfe(*chunks_, data=4096):
    end = True
    return b"NSFE" + b"".join(chunks_) + chunk("DATA", bytes(data)) + (chunk("NEND", b"") if end else b"")


def auth(*parts):
    return chunk("auth", b"".join(p.encode() + b"\0" for p in parts))


def tlbl(*labels):
    return chunk("tlbl", b"".join(l.encode() + b"\0" for l in labels))


def times(*ms):
    return chunk("time", struct.pack("<%di" % len(ms), *ms))
