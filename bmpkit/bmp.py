"""Windows / OS/2 bitmaps (.bmp): file header, DIB header versions, palettes, bit fields, row layout, RLE."""
import struct

HEADERS = {12: "core", 40: "info", 52: "v2", 56: "v3", 64: "os2v2", 108: "v4", 124: "v5"}
COMPRESSION = {0: "rgb", 1: "rle8", 2: "rle4", 3: "bitfields", 4: "jpeg", 5: "png", 6: "alphabitfields"}
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


def parse(data):
    if data[:2] != b"BM" or len(data) < 26:
        raise BmpError("not a BMP file")
    offset, = struct.unpack_from("<I", data, 10)
    hsize, = struct.unpack_from("<I", data, 14)
    if hsize not in HEADERS:
        raise BmpError("unknown DIB header size %d" % hsize)
    h0 = 14
    w, h, planes, bpp, comp, image_size, xppm, yppm, used = struct.unpack_from("<iiHHIIiiI", data, h0 + 4)
    entry = 4
    top_down = False
    masks = None
    if comp in (3, 6) or (bpp in (16, 32) and comp == 0):
        if bpp == 16:
            masks = [0x7C00, 0x03E0, 0x001F, 0]                 # BI_RGB 16-bit is 5-5-5
        else:
            masks = [0xFF0000, 0x00FF00, 0x0000FF, 0]           # BI_RGB 32-bit: the top byte is unused
    colors = 0
    if bpp <= 8:
        colors = used
    palette_at = h0 + hsize + (12 if comp == 3 and hsize == 40 else 16 if comp == 6 and hsize == 40 else 0)
    palette = [data[palette_at + i * entry:palette_at + i * entry + 3] for i in range(colors)]
    stride = (bpp * w + 7) // 8
    out = {"header": HEADERS[hsize], "width": w, "height": h, "top_down": top_down, "bit_count": bpp,
           "compression": COMPRESSION.get(comp, "type_%d" % comp), "palette_colors": colors,
           "row_bytes": stride if comp in (0, 3, 6) else None,
           "image_bytes": stride * h if comp in (0, 3, 6) else (image_size or len(data) - offset),
           "masks": ["%08x" % m for m in masks] if masks else None,
           "alpha": False, "dpi": [xppm, yppm],
           "color_space": None, "profile_bytes": None, "corner_pixel": None}
    if comp in (0, 3, 6) and w and h:
        row = 0 if top_down else h - 1                         # the stored row shown at the top of the picture
        p = offset + row * stride
        if p + stride <= len(data):
            if bpp == 8:
                idx = data[p]
                b, g, r = palette[idx][:3] if idx < len(palette) else (0, 0, 0)
            elif bpp < 8:
                return out
            elif bpp == 24:
                b, g, r = data[p:p + 3]
            else:
                v = int.from_bytes(data[p:p + bpp // 8], "little")
                r, g, b = (field(v, m) for m in (masks or [0x7C00, 0x03E0, 0x001F])[:3])
            out["corner_pixel"] = "#%02x%02x%02x" % (r, g, b)
    return out
