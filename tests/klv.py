"""Small GPMF tracks for the tests."""
import struct


def pad(raw):
    """GPMF pads every entry payload to a multiple of four bytes."""
    return raw + b"\0" * (-len(raw) % 4)


def kl(key, typ, raw, size=None, repeat=None):
    """One KLV entry. size/repeat default to the raw length (character data)."""
    if size is None:
        size, repeat = 1, len(raw)
    head = key.encode() + bytes([ord(typ) if typ != "\0" else 0, size]) + struct.pack(">H", repeat)
    return head + pad(raw)


def container(key, payload):
    return kl(key, "\0", payload, 1, len(payload))


def strm(*entries):
    return container("STRM", b"".join(entries))


def devc(*entries, dvid=1, name="CAMA"):
    head = kl("DVID", "L", struct.pack(">I", dvid)) + kl("DVNM", "c", name.encode())
    return container("DEVC", head + b"".join(entries))


def track(samples=4):
    data = kl("ACCL", "s", bytes(6 * samples), 6, samples)
    return devc(strm(data))
