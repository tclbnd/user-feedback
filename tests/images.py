"""A 2 x 1 uncompressed 24-bit TGA for the tests."""
import struct


def tiny():
    head = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 2, 1, 24, 0)
    return head + bytes([0, 0, 255, 0, 255, 0])
