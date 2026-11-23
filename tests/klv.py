"""Small GPMF tracks for the tests."""
import struct


def kl(key, typ, raw, size, repeat):
    """One KLV entry, with the padding GPMF requires after the payload."""
    head = key.encode() + bytes([ord(typ) if typ != "\0" else 0, size]) + struct.pack(">H", repeat)
    pad = -len(raw) % 4
    return head + raw + b"\0" * pad


def track(samples=4, payload=None, scale=None, name=b"CAMA", stnm=b"Accel", siun=None):
    if payload is None:
        payload = kl("ACCL", "s", bytes(6 * samples), 6, samples)
    strm = b""
    if stnm is not None:
        strm += kl("STNM", "c", stnm, 1, len(stnm))
    if siun is not None:
        strm += kl("SIUN", "c", siun, 1, len(siun))
    if scale is not None:
        strm += kl("SCAL", "s", struct.pack(">h", scale), 2, 1)
    strm += payload
    strm = kl("STRM", "\0", strm, 1, len(strm))
    dev = kl("DVID", "L", struct.pack(">I", 1), 4, 1)
    if name is not None:
        dev += kl("DVNM", "c", name, 1, len(name))
    dev += strm
    return kl("DEVC", "\0", dev, 1, len(dev))
