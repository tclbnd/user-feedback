"""Small NSV streams for the tests."""
import struct


def sync_frame(vlen=100, alen=20, rate=25):
    head = b"NSVs" + b"VP62" + b"MP3 " + struct.pack("<HHBH", 320, 240, rate, 0)
    word = vlen << 4
    return head + bytes([word & 0xFF, word >> 8 & 0xFF, word >> 16 & 0xFF]) + struct.pack("<H", alen) + bytes(vlen + alen)
