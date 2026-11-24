"""A minimal single-part header for the tests (no channels, no windows)."""
import struct


def minimal():
    a = b"compression\0compression\0" + struct.pack("<i", 1) + b"\x01"
    a += b"lineOrder\0lineOrder\0" + struct.pack("<i", 1) + b"\x00"
    return b"\x76\x2f\x31\x01" + struct.pack("<I", 2) + a + b"\0"
