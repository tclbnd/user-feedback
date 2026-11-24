"""Synthetic PCX images for the tests."""
import struct


def _header(version, encoding, bits, window, planes, bpl, palette=None, palinfo=1):
    head = bytearray(128)
    head[0], head[1], head[2], head[3] = 0x0A, version, encoding, bits
    struct.pack_into("<6H", head, 4, *window)
    if palette is not None:
        head[16:64] = palette
    head[65] = planes
    struct.pack_into("<HH", head, 66, bpl, palinfo)
    return bytes(head)


def tiny():
    head = bytearray(128)
    head[0], head[1], head[2], head[3] = 0x0A, 5, 0, 8
    struct.pack_into("<6H", head, 4, 0, 0, 1, 0, 72, 72)
    head[65] = 1
    struct.pack_into("<HH", head, 66, 2, 1)
    return bytes(head) + b"\x05\x06"


def offset_window():
    """10 x 1 image whose window starts at (4, 3)."""
    head = _header(5, 0, 8, (4, 3, 13, 3, 72, 72), 1, 10)
    return head + bytes(range(10))


def literal_high_bytes():
    """Literal bytes in 0x80-0xBF, which are not RLE markers (mono 8 x 1)."""
    head = _header(5, 1, 1, (0, 0, 7, 0, 72, 72), 1, 1)
    return head + b"\x80\xbf"


def run_across_lines():
    """A single RLE run that spans both scan lines of a 2 x 2 image."""
    head = _header(5, 1, 8, (0, 0, 1, 1, 72, 72), 1, 2)
    palette = bytearray(768)
    palette[21:24] = bytes([1, 2, 3])
    return head + b"\xc4\x07" + b"\x0c" + bytes(palette)


def vga256(palette):
    """1 x 1 8-bit image whose only pixel is palette index 1."""
    head = _header(5, 0, 8, (0, 0, 0, 0, 72, 72), 1, 1)
    body = b"\x01" + b"\x0c" + bytes(palette)
    return head + body


def truecolor(pixels):
    """2 x 1 8-bit image, three whole planes (R then G then B)."""
    head = _header(5, 0, 8, (0, 0, 1, 0, 72, 72), 3, 2)
    r, g, b = pixels
    return head + bytes([r, 0, g, 0, b, 0])


def ega16(palette):
    """1 x 1 4-plane image whose only pixel has planes 0 and 2 set (index 5)."""
    head = _header(2, 0, 1, (0, 0, 0, 0, 72, 72), 4, 1, palette=palette)
    return head + b"\x80\x00\x80\x00"

