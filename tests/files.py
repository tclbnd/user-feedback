"""Synthetic bitmaps covering every DIB header, pixel depth and storage variant."""
import struct


def _file(dib, pixels, offset=None, file_size=None):
    if offset is None:
        offset = 14 + len(dib)
    if file_size is None:
        file_size = offset + len(pixels)
    return b"BM" + struct.pack("<IHHI", file_size, 0, 0, offset) + dib + pixels


def tiny():
    """A 4 x 1 24-bit bottom-up bitmap (row length already a multiple of 4)."""
    pixels = bytes([0, 0, 255] * 4)
    dib = struct.pack("<IiiHHIIiiII", 40, 4, 1, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    return _file(dib, pixels)


def info_header(w, h, bpp, comp=0, image_size=0, xppm=2835, yppm=2835, used=0, colors_important=0):
    return struct.pack("<IiiHHIIiiII", 40, w, h, 1, bpp, comp, image_size, xppm, yppm,
                       used, colors_important)


def paletted(bpp, top_down=False, width=8, height=2, palette=None, rows=None, used=0):
    """BI_RGB paletted bitmap with a 40-byte info header; 1/4/8 bpp."""
    h = -height if top_down else height
    dib = info_header(width, h, bpp, used=used)
    if palette is None:
        # 2**bpp entries: entry i is blue=i, green=2*i, red=3*i (mod 256)
        palette = b"".join(bytes([i, (2 * i) & 0xFF, (3 * i) & 0xFF, 0])
                           for i in range(1 << bpp))
    dib += palette
    stride = ((bpp * width + 31) // 32) * 4
    if rows is None:
        first_byte = {1: 0x80, 4: 0x40, 8: 0x01}[bpp]
        rows = [bytes(first_byte for _ in range(stride)) for _ in range(height)]
    pixels = b"".join(rows)
    return _file(dib, pixels)


def os2_core(bpp=8, width=4, height=2, rows=None):
    """12-byte BITMAPCOREHEADER with 3-byte RGBTRIPLE palette entries."""
    dib = struct.pack("<IHHHH", 12, width, height, 1, bpp)
    palette = b"".join(bytes([i & 0xFF, (2 * i) & 0xFF, (3 * i) & 0xFF])
                       for i in range(1 << bpp))
    dib += palette
    stride = ((bpp * width + 31) // 32) * 4
    if rows is None:
        rows = [bytes(stride) for _ in range(height)]
    return _file(dib, b"".join(rows))


def os2_v2(bpp=8, width=4, height=2, comp=0, rows=None,
           masks=(0xF800, 0x07E0, 0x001F)):
    """64-byte OS/2 BITMAPINFOHEADER2 with RGBTRIPLE palette entries."""
    fixed = struct.pack("<IiiHHIIiiII", 64, width, height, 1, bpp, comp, 0, 2835, 2835, 0, 0)
    rest = struct.pack("<HHHHIIII", 0, 0, 0, 0, 0, 0, 0, 0)
    dib = fixed + rest
    if comp == 6:
        dib += struct.pack("<III", *masks)                # OS/2 bit fields: three masks, no alpha
    if bpp <= 8 and comp != 6:
        palette = b"".join(bytes([i & 0xFF, (2 * i) & 0xFF, (3 * i) & 0xFF])
                           for i in range(1 << bpp))
        dib += palette
    stride = ((bpp * width + 31) // 32) * 4
    if rows is None:
        fill = bytes([1]) if bpp == 8 else b"\0"
        rows = [fill + bytes(stride - len(fill)) for _ in range(height)]
    return _file(dib, b"".join(rows))


def bitfields_40(bpp=16, top_down=False, width=4, height=2,
                 masks=(0xF800, 0x07E0, 0x001F), alpha_mask=None, rows=None):
    """40-byte info header with masks stored right after the header."""
    comp = 6 if alpha_mask is not None else 3
    h = -height if top_down else height
    dib = info_header(width, h, bpp, comp=comp)
    dib += struct.pack("<III", *masks)
    if alpha_mask is not None:
        dib += struct.pack("<I", alpha_mask)
    stride = ((bpp * width + 31) // 32) * 4
    if rows is None:
        rows = [bytes(stride) for _ in range(height)]
    return _file(dib, b"".join(rows))


def v_header(hsize, bpp=32, comp=3, cs=0x73524742, masks=(0xFF0000, 0x00FF00, 0x0000FF, 0),
             profile_size=0, width=4, height=2, rows=None):
    """V2 (52), V3 (56), V4 (108) or V5 (124) header with in-header masks."""
    # rebuild the fixed part of the info header with the new DIB size
    fixed = struct.pack("<IiiHHIIiiII", hsize, width, height, 1, bpp, comp,
                        0, 2835, 2835, 0, 0)
    if hsize == 52:
        mask_dwords = struct.pack("<III", *masks[:3])       # V2: three masks
        dib = fixed + mask_dwords
    else:
        mask_dwords = struct.pack("<IIII", *masks)
        dib = fixed + mask_dwords
    if hsize >= 108:
        dib += struct.pack("<I", cs)                        # bV4CSType at DIB+56
        dib += bytes(36)                                    # CIEXYZTRIPLE endpoints
        dib += struct.pack("<III", 0, 0, 0)                 # gamma red/green/blue
    if hsize == 124:
        # V5 extension at DIB+108: bV5RenderingIntent, bV5ProfileData,
        # bV5ProfileSize (DIB offset 116), bV5Reserved
        dib += struct.pack("<IIII", 0, 0, profile_size, 0)
    stride = ((bpp * width + 31) // 32) * 4
    if rows is None:
        rows = [bytes(stride) for _ in range(height)]
    return _file(dib, b"".join(rows))

