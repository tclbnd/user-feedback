"""Small textures for the tests."""
import struct


def plain():
    """A 7.2 BGR888 texture of 32 x 16 with a single mipmap and no thumbnail."""
    head = bytearray(80)
    head[0:4] = b"VTF\0"
    struct.pack_into("<3I", head, 4, 7, 2, 80)
    struct.pack_into("<HHIHH", head, 16, 32, 16, 0, 1, 0)
    struct.pack_into("<I", head, 52, 3)
    head[56] = 1
    struct.pack_into("<I", head, 57, 0xFFFFFFFF)
    struct.pack_into("<H", head, 63, 1)
    return bytes(head) + bytes(32 * 16 * 3)
