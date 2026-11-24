"""Windows / OS/2 bitmaps (.bmp): file header, DIB header versions, palettes, bit fields, row layout, RLE.

Supports the Windows DIB headers from BITMAPCOREHEADER (OS/2 1.x, 12 bytes)
through BITMAPV5HEADER (124 bytes):
bottom-up and top-down storage, 1/4/8/16/24/32-bit pixels, RGB, RLE and
bit-field compression, OS/2 RGBTRIPLE palettes and default pixel masks.
"""
import struct

HEADERS = {12: "core", 40: "info", 52: "v2", 56: "v3", 64: "os2v2", 108: "v4", 124: "v5"}
COMPRESSION = {0: "rgb", 1: "rle8", 2: "rle4", 3: "bitfields", 4: "jpeg", 5: "png", 6: "alphabitfields"}
SPACES = {0: "calibrated", 0x73524742: "sRGB", 0x57696E20: "windows",
          0x4C494E4B: "linked", 0x4D424544: "embedded"}

# Default bit-field masks for uncompressed (BI_RGB) 16- and 32-bit pixels.
DEFAULT_MASKS = {16: [0x7C00, 0x03E0, 0x001F, 0],           # 5-5-5
                 32: [0x00FF0000, 0x0000FF00, 0x000000FF, 0]}
UNCOMPRESSED = (0, 3, 6)                                    # rgb, bitfields, alphabitfields


class BmpError(ValueError):
    pass


def field(v, mask):
    """Scale the bits under `mask` to 0-255 (rounding)."""
    if not mask:
        return None
    shift = (mask & -mask).bit_length() - 1
    width = bin(mask).count("1")
    x = (v & mask) >> shift
    return round(x * 255 / ((1 << width) - 1))


def parse(data):
    if data[:2] != b"BM" or len(data) < 26:
        raise BmpError("not a BMP file")
    offset, = struct.unpack_from("<I", data, 10)
    hsize, = struct.unpack_from("<I", data, 14)
    if hsize not in HEADERS:
        raise BmpError("unknown DIB header size %d" % hsize)
    h0 = 14                                                         # DIB header starts here

    # --- DIB header: BITMAPCOREHEADER (12 bytes) has a different layout. ---
    if hsize == 12:
        w, h = struct.unpack_from("<HH", data, h0 + 4)
        planes, bpp = struct.unpack_from("<HH", data, h0 + 8)
        comp, image_size, xppm, yppm, used = 0, 0, 0, 0, 0
    else:
        w, h, planes, bpp, comp, image_size, xppm, yppm, used = \
            struct.unpack_from("<iiHHIIiiI", data, h0 + 4)

    # Negative height means top-down rows (Windows headers only).
    top_down = h < 0
    height = abs(h)
    width = abs(w)

    # --- Bit-field masks: explicit masks, else the BI_RGB defaults. ---
    masks = None
    if bpp in (16, 32):
        if comp in (3, 6):
            if hsize == 40:
                # Masks are stored immediately after the 40-byte header.
                m_at = h0 + hsize
                masks = list(struct.unpack_from("<3I", data, m_at))
                if comp == 6:
                    masks.append(struct.unpack_from("<I", data, m_at + 12)[0])
                else:
                    masks.append(0)
            else:
                # V2/V3/V4/V5 carry the masks inside the header.
                masks = list(struct.unpack_from("<4I", data, h0 + 40))
        elif comp == 0:
            masks = list(DEFAULT_MASKS[bpp])

    # --- Colour table. ---
    if bpp <= 8:
        if used:
            colors = used
        elif hsize == 12:
            colors = 1 << bpp                                       # core header has no clrUsed field
        else:
            colors = 1 << bpp                                       # 0 means the full table
    else:
        colors = 0
    entry = 3 if hsize == 12 else 4                                 # RGBTRIPLE vs RGBQUAD
    extra = 0
    if hsize == 40 and comp in (3, 6):
        extra = 16 if comp == 6 else 12                             # masks after an info header
    palette_at = h0 + hsize + extra
    palette = [data[palette_at + i * entry:palette_at + i * entry + 3]
               for i in range(colors)]

    # --- Row storage: every row is padded out to a multiple of 4 bytes. ---
    pixel_bytes = (bpp * width + 7) // 8
    stride = (pixel_bytes + 3) // 4 * 4
    uncompressed = comp in UNCOMPRESSED

    out = {
        "header": HEADERS[hsize],
        "width": width,
        "height": height,
        "top_down": top_down,
        "bit_count": bpp,
        "compression": COMPRESSION.get(comp, "type_%d" % comp),
        "palette_colors": colors,
        "row_bytes": stride if uncompressed else None,
        "image_bytes": stride * height if uncompressed
                       else (image_size or max(0, len(data) - offset)),
        "masks": ["%08x" % m for m in masks] if masks is not None else None,
        "alpha": bool(masks and masks[3]),
        "dpi": [round(xppm * 0.0254), round(yppm * 0.0254)],
        "color_space": None,
        "profile_bytes": None,
        "corner_pixel": None,
    }

    # --- V4/V5 colour space and V5 ICC profile data. ---
    if hsize >= 108:
        cs, = struct.unpack_from("<I", data, h0 + 56)
        out["color_space"] = SPACES.get(cs, "type_%08x" % cs)
        if hsize == 124 and cs in (0x4D424544, 0x4C494E4B):
            out["profile_bytes"], = struct.unpack_from("<I", data, h0 + 116)

    # --- Colour of the pixel shown at the top-left of the picture. ---
    if uncompressed and width and height:
        row = 0 if top_down else height - 1
        p = offset + row * stride
        if p < len(data):
            if bpp <= 8:
                byte = data[p]
                idx = byte >> (8 - bpp) if bpp < 8 else byte
                b, g, r = palette[idx] if idx < len(palette) else (0, 0, 0)
            elif bpp == 24:
                b, g, r = data[p:p + 3]
            else:
                v = int.from_bytes(data[p:p + bpp // 8], "little")
                r, g, b = (field(v, m) for m in masks[:3])
            out["corner_pixel"] = "#%02x%02x%02x" % (r, g, b)
    return out
