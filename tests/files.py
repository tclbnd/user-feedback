"""Hand-built bitmaps for the tests."""
import struct


def _file(dib, pixels, palette=b"", extra=b""):
    offset = 14 + len(dib) + len(extra) + len(palette)
    return b"BM" + struct.pack("<IHHI", offset + len(pixels), 0, 0, offset) + dib + extra + palette + pixels


def info(width, height, bpp, comp=0, used=0, image_size=None, xppm=2835, yppm=2835,
         masks=None, pixels=b"", palette=b""):
    """A 40-byte BITMAPINFOHEADER bitmap; `masks` are the DWORDs that follow it."""
    extra = b"".join(struct.pack("<I", m) for m in masks or ())
    if image_size is None:
        image_size = len(pixels)
    dib = struct.pack("<IiiHHIIiiII", 40, width, height, 1, bpp, comp, image_size, xppm, yppm, used, 0)
    return _file(dib, pixels, palette, extra)


def core(width, height, bpp, pixels=b"", palette=b""):
    """An OS/2 1.x BITMAPCOREHEADER (12 bytes) bitmap with 3-byte palette entries."""
    dib = struct.pack("<IHHHH", 12, width, height, 1, bpp)
    return _file(dib, pixels, palette)


def os2v2(width, height, bpp, comp=0, used=0, units=0, res=2835, pixels=b"", palette=b""):
    """An OS/2 2.x BITMAPCOREHEADER2 (64 bytes) bitmap with RGB2 palette entries."""
    dib = struct.pack("<IIIHHIIIIII", 64, width, height, 1, bpp, comp, len(pixels), res, res, used, 0)
    dib += struct.pack("<HHHH", units, 0, 0, 0) + b"\0" * 16
    return _file(dib, pixels, palette)


def v2(width, height, bpp, masks=(0, 0, 0), pixels=b"", palette=b""):
    """A 52-byte BITMAPV2INFOHEADER: three masks and no colour space."""
    dib = struct.pack("<IiiHHIIiiII", 52, width, height, 1, bpp, 0, len(pixels), 2835, 2835, 0, 0)
    dib += struct.pack("<III", *masks)
    return _file(dib, pixels, palette)


def v3(width, height, bpp, masks=(0, 0, 0, 0), pixels=b"", palette=b""):
    """A 56-byte BITMAPV3INFOHEADER: four masks and no colour space."""
    dib = struct.pack("<IiiHHIIiiII", 56, width, height, 1, bpp, 0, len(pixels), 2835, 2835, 0, 0)
    dib += struct.pack("<IIII", *masks)
    return _file(dib, pixels, palette)


def v4(width, height, bpp, comp=0, masks=(0, 0, 0, 0), space=0x73524742, pixels=b"", palette=b""):
    dib = struct.pack("<IiiHHIIiiII", 108, width, height, 1, bpp, comp, len(pixels), 2835, 2835, 0, 0)
    dib += struct.pack("<IIII", *masks)
    dib += struct.pack("<I", space) + b"\0" * 36 + struct.pack("<III", 0, 0, 0)
    return _file(dib, pixels, palette)


def v5(width, height, bpp, comp=0, masks=(0, 0, 0, 0), space=0x73524742, profile=0, pixels=b"", palette=b""):
    dib = struct.pack("<IiiHHIIiiII", 124, width, height, 1, bpp, comp, len(pixels), 2835, 2835, 0, 0)
    dib += struct.pack("<IIII", *masks)
    dib += struct.pack("<I", space) + b"\0" * 36 + struct.pack("<III", 0, 0, 0)
    dib += struct.pack("<IIII", 4, 0, profile, 0)
    return _file(dib, pixels, palette)


def entries(*rgb):
    """Colour table entries (blue, green, red, unused) from (r, g, b) tuples."""
    return b"".join(bytes([b, g, r, 0]) for r, g, b in rgb)


def entries3(*rgb):
    """OS/2 colour table entries (blue, green, red) from (r, g, b) tuples."""
    return b"".join(bytes([b, g, r]) for r, g, b in rgb)


def tiny():
    pixels = bytes([0, 0, 255] * 4)
    dib = struct.pack("<IiiHHIIiiII", 40, 4, 1, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    return b"BM" + struct.pack("<IHHI", 54 + len(pixels), 0, 0, 54) + dib + pixels
