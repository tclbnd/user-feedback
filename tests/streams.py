"""Builders for small synthetic Musepack SV8 streams used by the tests."""
import struct
import zlib


def varint(v):
    out = [v & 0x7F]
    v >>= 7
    while v:
        out.insert(0, 0x80 | (v & 0x7F))
        v >>= 7
    return bytes(out)


def packet(key, payload):
    n = 1
    while True:
        total = 2 + n + len(payload)
        v = varint(total)
        if len(v) == n:
            return key + v + payload
        n = len(v)


def sh(samples, silence=0, rate=44100, channels=2, bands=26, ms=True, block=1):
    bad_crc, version = False, 8
    ri = (44100, 48000, 37800, 32000).index(rate)
    body = bytes([version]) + varint(samples) + varint(silence)
    body += bytes([(ri << 5) | (bands - 1), ((channels - 1) << 4) | (8 if ms else 0) | block])
    crc = zlib.crc32(body) & 0xFFFFFFFF
    return packet(b"SH", struct.pack(">I", crc ^ (1 if bad_crc else 0)) + body)


def rg(title_gain=-1280, title_peak=30000, album_gain=-900, album_peak=32000):
    return packet(b"RG", struct.pack(">BhHhH", 1, title_gain, title_peak, album_gain, album_peak))


def ei(profile=8.0, version=(1, 30, 0)):
    pns = False
    return packet(b"EI", bytes([(int(profile * 8) << 1) | (1 if pns else 0)]) + bytes(version))


def ap(size=300, seed=0):
    return packet(b"AP", bytes(((i * 13 + seed) & 0xFF) for i in range(size)))


def ct(title="Chapter"):
    return packet(b"CT", varint(0) + title.encode())


def se():
    return packet(b"SE", b"")


def so(offset=100):
    return packet(b"SO", varint(offset))


def mpc(*packets):
    return b"MPCK" + b"".join(packets)


def stream(samples=44100 * 10, napk=None, size=100, **kw):
    block = kw.get("block", 1)
    napk = napk if napk is not None else -(-samples // (1152 * 2 ** block))
    return mpc(sh(samples, **kw), rg(), ei(), so(), *[ap(size, k) for k in range(napk)], se())
