"""Windows / OS/2 bitmaps (.bmp): file header, DIB header versions, palettes, bit fields, row layout, RLE."""
import struct

HEADERS = {12: "core", 40: "info", 52: "v2", 56: "v3", 64: "os2v2", 108: "v4", 124: "v5"}
# Windows BI_* compression codes.
COMPRESSION = {0: "rgb", 1: "rle8", 2: "rle4", 3: "bitfields", 4: "jpeg", 5: "png", 6: "alphabitfields"}
# OS/2 BITMAPINFOHEADER2 uses its own compression codes.
OS2_COMPRESSION = {0: "rgb", 1: "rle8", 2: "rle4", 3: "huffman1d", 4: "rle24", 6: "bitfields"}
SPACES = {0x73524742: "sRGB", 0x57696E20: "windows", 0x4C494E4B: "linked",
          0x4D424544: "embedded", 0: "calibrated"}


class BmpError(ValueError):
    pass


def field(v, mask):
    """Scale the bits under `mask` to 0-255."""
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
    header = HEADERS[hsize]
    h0 = 14

    if header == "core":
        # BITMAPCOREHEADER: 16-bit unsigned dimensions, no compression field.
        w, h, planes, bpp = struct.unpack_from("<HHHH", data, h0 + 4)
        comp = 0
        image_size = xppm = yppm = used = 0
    else:
        w, h, planes, bpp, comp, image_size, xppm, yppm, used = \
            struct.unpack_from("<iiHHIIiiI", data, h0 + 4)

    top_down = h < 0
    height = abs(h)
    compression_name = (OS2_COMPRESSION if header == "os2v2" else COMPRESSION).get(comp, "type_%d" % comp)

    # Colour table entries are RGBTRIPLE (3 bytes) in OS/2 headers, RGBQUAD (4) elsewhere.
    entry = 3 if header in ("core", "os2v2") else 4

    # Bit-field masks: defaults for plain 16/32-bit BI_RGB, otherwise read
    # from the header (V2+) or the DWORDs just after a short info header.
    masks = None
    bitfields_comp = comp in (3, 6)
    if bpp in (16, 32) and comp == 0:
        if bpp == 16:
            masks = [0x7C00, 0x03E0, 0x001F, 0]              # BI_RGB 16-bit is 5-5-5
        else:
            masks = [0xFF0000, 0x00FF00, 0x0000FF, 0]        # BI_RGB 32-bit: the top byte is unused
    elif bitfields_comp:
        if hsize in (56, 108, 124):
            # Windows V3/V4/V5 headers carry all four masks in the header.
            masks = list(struct.unpack_from("<IIII", data, h0 + 40))
        elif hsize == 52:
            # V2 header holds the red, green and blue masks only.
            masks = list(struct.unpack_from("<III", data, h0 + 40)) + [0]
        else:
            # 40-byte info header and the 64-byte OS/2 header: the mask
            # DWORDs follow the whole header. BI_BITFIELDS stores three of
            # them; BI_ALPHABITFIELDS (info only) adds an alpha mask.
            rmask, gmask, bmask = struct.unpack_from("<III", data, h0 + hsize)
            amask = struct.unpack_from("<I", data, h0 + hsize + 12)[0] \
                if comp == 6 and hsize == 40 else 0
            masks = [rmask, gmask, bmask, amask]

    # A clrUsed of 0 means the full 2**bpp colour table for paletted images.
    colors = 0
    if bpp <= 8:
        colors = used or (1 << bpp)

    # Masks stored after a short header sit between the header and the palette:
    # three DWORDs for BI_BITFIELDS (and the OS/2 variant), plus an alpha DWORD
    # for BI_ALPHABITFIELDS on a 40-byte info header.
    if bitfields_comp and hsize in (40, 64):
        mask_bytes = 16 if comp == 6 and hsize == 40 else 12
    else:
        mask_bytes = 0
    palette_at = h0 + hsize + mask_bytes
    palette = [data[palette_at + i * entry:palette_at + i * entry + 3] for i in range(colors)]

    # Rows are padded to a 4-byte boundary (BITMAPCOREHEADER included).
    stride = ((bpp * w + 31) // 32) * 4
    uncompressed = comp == 0 or bitfields_comp

    dpi = [round(xppm * 0.0254), round(yppm * 0.0254)]
    out = {"header": header, "width": w, "height": height, "top_down": top_down,
           "bit_count": bpp, "compression": compression_name, "palette_colors": colors,
           "row_bytes": stride if uncompressed else None,
           "image_bytes": stride * height if uncompressed else (image_size or len(data) - offset),
           "masks": ["%08x" % m for m in masks] if masks else None,
           "alpha": bool(masks and masks[3]),
           "dpi": dpi,
           "color_space": None, "profile_bytes": None, "corner_pixel": None}

    # V4/V5 colour space and, for V5, linked/embedded ICC profile size.
    if header in ("v4", "v5"):
        cs_type, = struct.unpack_from("<I", data, h0 + 56)
        out["color_space"] = SPACES.get(cs_type, "type_%08x" % cs_type)
        if header == "v5" and cs_type in (0x4C494E4B, 0x4D424544):
            # bV5ProfileSize sits at DIB offset 116 (file offset 130).
            out["profile_bytes"], = struct.unpack_from("<I", data, h0 + 116)

    if uncompressed and w and height:
        row = 0 if top_down else height - 1                    # the stored row shown at the top of the picture
        p = offset + row * stride
        if p + stride <= len(data):
            r = g = b = None
            if bpp <= 8:
                if bpp == 1:
                    idx = (data[p] >> 7) & 1                   # MSB holds the leftmost pixel
                elif bpp == 4:
                    idx = data[p] >> 4                         # high nibble holds the leftmost pixel
                else:
                    idx = data[p]
                b, g, r = palette[idx][:3] if idx < len(palette) else (0, 0, 0)
            elif bpp == 24:
                b, g, r = data[p:p + 3]
            else:
                v = int.from_bytes(data[p:p + bpp // 8], "little")
                r, g, b = (field(v, m) for m in (masks or [0x7C00, 0x03E0, 0x001F, 0])[:3])
            if r is not None and g is not None and b is not None:
                out["corner_pixel"] = "#%02x%02x%02x" % (r, g, b)
    return out
