"""Minimal version 1 tunes for the tests (big-endian header, data at 0x76)."""
import struct


def tune(load=0x1000, songs=1):
    h = bytearray(0x76)
    h[:4] = b"PSID"
    struct.pack_into(">HHHHHHHI", h, 4, 1, 0x76, load, load, load + 3, songs, 1, 0)
    h[0x16:0x1A] = b"Test"
    return bytes(h) + bytes(16)
