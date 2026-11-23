"""Small GPMF tracks for the tests."""
import struct


def kl(key, typ, raw, size, repeat):
    head = key.encode() + bytes([ord(typ) if typ != "\0" else 0, size]) + struct.pack(">H", repeat)
    return head + raw


def track(samples=4):
    data = kl("ACCL", "s", bytes(6 * samples), 6, samples)
    strm = kl("STRM", "\0", data, 1, len(data))
    dev = kl("DVNM", "c", b"CAMA", 4, 1) + strm
    return kl("DEVC", "\0", dev, 1, len(dev))
