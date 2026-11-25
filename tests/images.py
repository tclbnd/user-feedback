"""A 2 x 1 uncompressed 8-bit PCX without a VGA palette for the tests."""
import struct


def tiny():
    head = bytearray(128)
    head[0], head[1], head[2], head[3] = 0x0A, 5, 0, 8
    struct.pack_into("<6H", head, 4, 0, 0, 1, 0, 72, 72)
    head[65] = 1
    struct.pack_into("<HH", head, 66, 2, 1)
    return bytes(head) + b"\x05\x06"
