"""Windows / OS/2 bitmaps (.bmp): file header, DIB header versions, palettes, bit fields, row layout, RLE."""
import struct

HEADERS = {12: "core", 40: "info", 52: "v2", 56: "v3", 64: "os2v2", 108: "v4", 124: "v5"}
COMPRESSION = {0: "rgb", 1: "rle8", 2: "rle4", 3: "bitfields", 4: "jpeg", 5: "png", 6: "alphabitfields"}
OS2_COMPRESSION = {0: "rgb", 1: "rle8", 2: "rle4", 3: "huffman1d", 4: "rle24"}
SPACES = {0x73524742: "sRGB", 0x57696E20: "windows", 0x4C494E4B: "linked", 0x4D424544: "embedded", 0: "calibrated"}


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


def row_bytes(bpp, width):
    """Bytes per stored row: rows are padded to a 4-byte boundary."""
    return ((bpp * width + 31) // 32) * 4


def parse(data):
    if data[:2] != b"BM" or len(data) < 26:
        raise BmpError("not a BMP file")
    offset, = struct.unpack_from("<I", data, 10)
    hsize, = struct.unpack_from("<I", data, 14)
    if hsize not in HEADERS:
        raise BmpError("unknown DIB header size %d" % hsize)
    h0 = 14
    entry = 4
    top_down = False
    image_size = 0
    xppm = yppm = 0
    used = 0
    inches = False
    if hsize == 12:
        # OS/2 1.x BITMAPCOREHEADER: 16-bit dimensions, 3-byte palette entries,
        # no compression or resolution.
        w, h, planes, bpp = struct.unpack_from("<HHHH", data, h0 + 4)
        comp = 0
        entry = 3
    elif hsize == 64:
        # OS/2 2.x BITMAPCOREHEADER2: 32-bit dimensions, own compression numbering,
        # palette entries are RGB2; still no negative height.
        w, h, planes, bpp, comp, image_size, xppm, yppm, used = struct.unpack_from("<IIHHIIIII", data, h0 + 4)
        entry = 3
        units, = struct.unpack_from("<H", data, h0 + 40)
        inches = units == 1                            # pels per inch rather than per metre
    else:
        # Windows BITMAPINFOHEADER and later; a negative height means top-down rows.
        w, h, planes, bpp, comp, image_size, xppm, yppm, used = struct.unpack_from("<iiHHIIiiI", data, h0 + 4)
        top_down = h < 0
    height = abs(h)

    if hsize == 12:
        compression = COMPRESSION[0]
        uncompressed = True
    elif hsize == 64:
        compression = OS2_COMPRESSION.get(comp, "type_%d" % comp)
        uncompressed = comp == 0
    else:
        compression = COMPRESSION.get(comp, "type_%d" % comp)
        uncompressed = comp in (0, 3, 6)

    # Bit-field masks: in V2 and later they are part of the header itself; after a 40-byte
    # header they are the three or four DWORDs preceding the colour table.
    masks = None
    if bpp in (16, 32) and uncompressed:
        values = None
        if hsize in (52, 56, 108, 124):
            # The 52-byte V2 header holds the three colour masks only; V3 adds alpha.
            count = 3 if hsize == 52 else 4
            values = list(struct.unpack_from("<%dI" % count, data, h0 + 40)) + [0] * (4 - count)
            if not any(values[:3]):
                values = None                       # masks not filled in: use the defaults
        elif hsize == 40 and comp in (3, 6):
            count = 4 if comp == 6 else 3
            values = list(struct.unpack_from("<%dI" % count, data, h0 + 40)) + [0] * (4 - count)
        if values is None:
            # BI_RGB defaults: X1R5G5B5 for 16 bits, X8R8G8B8 for 32 bits.
            values = [0x7C00, 0x03E0, 0x001F, 0] if bpp == 16 else [0xFF0000, 0x00FF00, 0x0000FF, 0]
        masks = values

    colors = 0
    if bpp <= 8:
        # A colour count of 0 means the full 2**bpp table; larger counts are not meaningful.
        colors = min(used, 1 << bpp) if used else 1 << bpp

    palette_at = h0 + hsize
    if hsize == 40 and comp in (3, 6):
        palette_at += 12 if comp == 3 else 16
    palette = [data[palette_at + i * entry:palette_at + i * entry + 3] for i in range(colors)]

    stride = row_bytes(bpp, w)
    if inches:
        dpi = [xppm, yppm]
    else:
        dpi = [round(xppm * 0.0254), round(yppm * 0.0254)]
    out = {"header": HEADERS[hsize], "width": w, "height": height, "top_down": top_down, "bit_count": bpp,
           "compression": compression, "palette_colors": colors,
           "row_bytes": stride if uncompressed else None,
           "image_bytes": stride * height if uncompressed else (image_size or max(0, len(data) - offset)),
           "masks": ["%08x" % m for m in masks] if masks else None,
           "alpha": bool(masks and masks[3]),
           "dpi": dpi,
           "color_space": None, "profile_bytes": None, "corner_pixel": None}

    if hsize in (108, 124):
        space, = struct.unpack_from("<I", data, h0 + 56)
        out["color_space"] = SPACES.get(space, "type_%08x" % space)
        if hsize == 124 and space in (0x4C494E4B, 0x4D424544):
            size, = struct.unpack_from("<I", data, h0 + 116)
            out["profile_bytes"] = size

    if uncompressed and w and height:
        row = 0 if top_down else height - 1        # stored row shown at the top of the picture
        p = offset + row * stride
        r = g = b = None
        if bpp in (1, 4, 8):
            if p < len(data):
                byte = data[p]
                if bpp == 1:
                    idx = (byte >> 7) & 0x01
                elif bpp == 4:
                    idx = (byte >> 4) & 0x0F
                else:
                    idx = byte
                item = palette[idx] if idx < len(palette) else b""
                if len(item) == 3:
                    b, g, r = item
                else:
                    b = g = r = 0
        elif bpp == 24:
            if p + 3 <= len(data):
                b, g, r = data[p:p + 3]
        elif bpp in (16, 32) and masks:
            n = bpp // 8
            if p + n <= len(data):
                v = int.from_bytes(data[p:p + n], "little")
                r, g, b = (field(v, m) or 0 for m in masks[:3])
        if r is not None:
            out["corner_pixel"] = "#%02x%02x%02x" % (r, g, b)
    return out
