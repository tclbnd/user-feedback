"""A 4 x 1 24-bit bottom-up bitmap for the tests (row length already a multiple of 4)."""
import struct


def tiny():
    pixels = bytes([0, 0, 255] * 4)
    dib = struct.pack("<IiiHHIIiiII", 40, 4, 1, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    return b"BM" + struct.pack("<IHHI", 54 + len(pixels), 0, 0, 54) + dib + pixels
