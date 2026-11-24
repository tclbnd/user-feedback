"""Minimal GBS rips for the tests."""


def rip(songs=3, title=b"Test"):
    h = bytearray(0x70)
    h[:3] = b"GBS"
    h[3], h[4], h[5] = 1, songs, 1
    h[0x10:0x10 + len(title)] = title
    return bytes(h) + bytes(64)
