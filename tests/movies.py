"""A header-only RoQ movie."""
import struct


def empty():
    return struct.pack("<HIH", 0x1084, 0xFFFFFFFF, 30)
