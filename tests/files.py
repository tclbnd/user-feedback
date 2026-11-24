"""Bitmap fixtures for the tests."""
import struct


def _wrap(dib, gap, pixels):
    """File header around a DIB header, the mask/palette gap and the pixel data."""
    off = 14 + len(dib) + len(gap)
    return b"BM" + struct.pack("<IHHI", off + len(pixels), 0, 0, off) + dib + gap + pixels


def tiny():
    """A 4 x 1 24-bit bottom-up bitmap (row length already a multiple of 4)."""
    pixels = bytes([0, 0, 255] * 4)
    dib = struct.pack("<IiiHHIIiiII", 40, 4, 1, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    return _wrap(dib, b"", pixels)


def one_bit():
    """An 8 x 1 1-bit bitmap; the top-left pixel is palette index 1 (white)."""
    palette = bytes([0, 0, 0, 0, 255, 255, 255, 0])
    pixels = bytes([0b10000000, 0, 0, 0])
    dib = struct.pack("<IiiHHIIiiII", 40, 8, 1, 1, 1, 0, len(pixels), 0, 0, 0, 0)
    return _wrap(dib, palette, pixels)


def four_bit():
    """A 4 x 1 4-bit bitmap; the top-left pixel is palette index 3."""
    palette = b"".join(bytes([i * 16, 0, 255 - i * 16, 0]) for i in range(16))
    pixels = bytes([0x30, 0x00])
    dib = struct.pack("<IiiHHIIiiII", 40, 4, 1, 1, 4, 0, len(pixels), 0, 0, 0, 0)
    return _wrap(dib, palette, pixels)


def eight_bit_default_palette():
    """A 1 x 1 8-bit bitmap with the colour count left as 0 (meaning 256)."""
    palette = b"".join(bytes([i, i, i, 0]) for i in range(256))
    pixels = bytes([5, 0, 0, 0])
    dib = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 8, 0, len(pixels), 0, 0, 0, 0)
    return _wrap(dib, palette, pixels)


def top_down():
    """A 4 x 2 24-bit bitmap with a negative height: the top row is stored first."""
    pixels = bytes([0, 0, 255] * 4) + bytes([255, 0, 0] * 4)
    dib = struct.pack("<IiiHHIIiiII", 40, 4, -2, 1, 24, 0, len(pixels), 0, 0, 0, 0)
    return _wrap(dib, b"", pixels)


def aligned():
    """A 3 x 1 24-bit bitmap: 9 data bytes padded to a 12-byte row."""
    pixels = bytes([0, 255, 0] * 3) + b"\x00" * 3
    dib = struct.pack("<IiiHHIIiiII", 40, 3, 1, 1, 24, 0, len(pixels), 0, 0, 0, 0)
    return _wrap(dib, b"", pixels)


def bitfields():
    """A 2 x 1 16-bit BI_BITFIELDS bitmap, 5-6-5 masks after the header; top-left pixel green."""
    masks = struct.pack("<III", 0xF800, 0x07E0, 0x001F)
    pixels = struct.pack("<HH", 0x07E0, 0)
    dib = struct.pack("<IiiHHIIiiII", 40, 2, 1, 1, 16, 3, len(pixels), 0, 0, 0, 0)
    return _wrap(dib, masks, pixels)


def core():
    """An OS/2 1.x BITMAPCOREHEADER 2 x 1 24-bit bitmap; top-left pixel green."""
    dib = struct.pack("<IHHHH", 12, 2, 1, 1, 24)
    pixels = bytes([0, 255, 0, 0, 0, 255]) + b"\x00" * 2
    return _wrap(dib, b"", pixels)


def core_4bit():
    """An OS/2 1.x 4-bit bitmap with 3-byte palette entries; top-left pixel index 1 (red)."""
    dib = struct.pack("<IHHHH", 12, 2, 1, 1, 4)
    palette = bytes([0, 0, 0, 0, 0, 255]) + b"\x00" * (3 * 14)
    pixels = bytes([0x10, 0, 0, 0])
    return _wrap(dib, palette, pixels)


def v4():
    """A BITMAPV4HEADER 1 x 1 32-bit bitmap with an alpha mask, sRGB, 72 dpi; blue pixel."""
    dib = struct.pack("<IiiHHIIiiII", 108, 1, 1, 1, 32, 3, 4, 2835, 2835, 0, 0)
    dib += struct.pack("<IIII", 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    dib += struct.pack("<I", 0x73524742)                        # sRGB
    dib += b"\x00" * (108 - len(dib))
    return _wrap(dib, b"", bytes([255, 0, 0, 255]))


def v5():
    """A BITMAPV5HEADER 1 x 1 24-bit bitmap with a 640-byte embedded ICC profile; green pixel."""
    dib = struct.pack("<IiiHHIIiiII", 124, 1, 1, 1, 24, 0, 4, 0, 0, 0, 0)
    dib += struct.pack("<IIII", 0, 0, 0, 0)                     # masks
    dib += struct.pack("<I", 0x4D424544)                        # embedded profile
    dib += b"\x00" * 48                                         # endpoints and gamma
    dib += struct.pack("<IIII", 0, 0, 640, 0)                   # intent, profile data, size, reserved
    return _wrap(dib, b"", bytes([0, 255, 0, 0]))
