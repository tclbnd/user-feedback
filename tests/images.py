"""Synthetic TGA images for the tests."""
import struct

from tgakit.tga import SIGNATURE


def tiny():
    """A 2 x 1 uncompressed 24-bit TGA."""
    head = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 2, 1, 24, 0)
    return head + bytes([0, 0, 255, 0, 255, 0])


def rle_gray():
    """A 3 x 1 RLE 8-bit grayscale TGA: one run packet of three pixels."""
    head = struct.pack("<BBBHHBHHHHBB", 0, 0, 11, 0, 0, 0, 0, 0, 3, 1, 8, 0)
    return head + bytes([0x82, 7])


def palette():
    """A 2 x 1 colour-mapped TGA with an image ID and a two-entry 24-bit map, top-left origin."""
    head = struct.pack("<BBBHHBHHHHBB", 4, 1, 1, 0, 2, 24, 0, 0, 2, 1, 8, 0x20)
    cmap = bytes([0, 0, 255, 0, 255, 0])    # entry 0 red, entry 1 green (BGR)
    return head + b"tile" + cmap + bytes([0, 1])


def rgb555():
    """A 1 x 1 uncompressed 16-bit TGA: red 31 with the attribute bit set."""
    head = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 1, 1, 16, 1)
    return head + struct.pack("<H", 0x8000 | 31 << 10)


def tga2():
    """A 1 x 1 24-bit TGA with a TGA 2.0 extension area, thumbnail and footer."""
    head = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 1, 1, 24, 0)
    pixel = bytes([1, 2, 3])
    ext_off = len(head) + len(pixel)
    e = bytearray(495)
    struct.pack_into("<H", e, 0, 495)
    e[2:11] = b"Jane Doe\0"
    e[43:53] = b"first line"
    e[124:134] = b"last line\0"
    struct.pack_into("<6H", e, 367, 3, 4, 2025, 5, 6, 7)
    e[379:387] = b"shot 12\0"
    e[426:435] = b"PaintBox\0"
    struct.pack_into("<HB", e, 467, 410, ord("b"))
    struct.pack_into("<2H", e, 474, 4, 3)
    struct.pack_into("<2H", e, 478, 22, 10)
    struct.pack_into("<I", e, 486, ext_off + 495)
    e[494] = 3
    thumb = bytes([8, 8]) + bytes(8 * 8 * 3)
    return head + pixel + bytes(e) + thumb + struct.pack("<II", ext_off, 0) + SIGNATURE
