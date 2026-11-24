"""Windows / OS/2 bitmaps (.bmp): file header, DIB header versions, palettes, bit fields, row layout, RLE."""
import struct

HEADERS = {12: "core", 40: "info", 52: "v2", 56: "v3", 64: "os2v2", 108: "v4", 124: "v5"}
COMPRESSION = {0: "rgb", 1: "rle8", 2: "rle4", 3: "bitfields", 4: "jpeg", 5: "png", 6: "alphabitfields"}
CORE_COMPRESSION = {0: "rgb", 1: "rle8", 2: "rle4", 3: "huffman1d", 4: "rle24"}   # OS/2 1.x meanings
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
    core = hsize == 12
    if core:                                                     # OS/2 1.x BITMAPCOREHEADER
        w, h, planes, bpp = struct.unpack_from("<HHHH", data, h0 + 4)
        comp, image_size, xppm, yppm, used = 0, 0, 0, 0, 0
        top_down = False
    else:
        w, h, planes, bpp, comp, image_size, xppm, yppm, used = struct.unpack_from("<iiHHIIiiI", data, h0 + 4)
        top_down = h < 0                                         # a negative height means top row first
        h = abs(h)
    entry = 3 if core else 4                                     # core palettes are RGBTRIPLEs
    names = CORE_COMPRESSION if core else COMPRESSION
    raw = comp == 0 or (not core and comp in (3, 6))             # uncompressed pixel data

    masks = None
    if not core and bpp in (16, 32):
        if comp in (3, 6):
            if hsize >= 56:                                      # v3/v4/v5 keep the masks in the header
                masks = list(struct.unpack_from("<IIII", data, h0 + 40))
            elif hsize == 52:                                    # v2 has red/green/blue only
                masks = list(struct.unpack_from("<III", data, h0 + 40)) + [0]
            else:                                                # 40-byte header: masks follow it
                n = 4 if comp == 6 else 3
                masks = list(struct.unpack_from("<%dI" % n, data, h0 + hsize)) + [0] * (4 - n)
        elif bpp == 16:
            masks = [0x7C00, 0x03E0, 0x001F, 0]                  # BI_RGB 16-bit is 5-5-5
        else:
            masks = [0xFF0000, 0x00FF00, 0x0000FF, 0]            # BI_RGB 32-bit: the top byte is unused
    colors = 0
    if bpp <= 8:
        colors = used or (1 << bpp)                              # 0 means the full table for this depth
    gap = 0
    if not core and comp in (3, 6) and hsize < 52:
        gap = 16 if comp == 6 else 12                            # mask DWORDs between header and palette
    palette_at = h0 + hsize + gap
    palette = [data[palette_at + i * entry:palette_at + i * entry + 3] for i in range(colors)]

    stride = ((bpp * w + 31) // 32) * 4                          # rows are padded to a 4-byte boundary
    color_space = None
    profile_bytes = None
    if hsize >= 108:
        cs, = struct.unpack_from("<I", data, h0 + 56)
        color_space = SPACES.get(cs, "type_%08x" % cs)
        if hsize == 124 and cs in (0x4C494E4B, 0x4D424544):      # linked / embedded ICC profile
            profile_bytes, = struct.unpack_from("<I", data, h0 + 116)

    out = {"header": HEADERS[hsize], "width": w, "height": h, "top_down": top_down, "bit_count": bpp,
           "compression": names.get(comp, "type_%d" % comp), "palette_colors": colors,
           "row_bytes": stride if raw else None,
           "image_bytes": stride * h if raw else (image_size or len(data) - offset),
           "masks": ["%08x" % m for m in masks] if masks else None,
           "alpha": bool(masks and masks[3]),
           "dpi": [round(xppm * 0.0254), round(yppm * 0.0254)],  # pixels per metre to dots per inch
           "color_space": color_space, "profile_bytes": profile_bytes, "corner_pixel": None}
    if raw and w and h:
        row = 0 if top_down else h - 1                           # the stored row shown at the top of the picture
        p = offset + row * stride
        if p + (bpp + 7) // 8 <= len(data):
            rgb = None
            if bpp == 1:
                idx = (data[p] >> 7) & 1                         # the left-most pixel is the top bit
                rgb = palette[idx][:3] if idx < len(palette) else None
            elif bpp == 4:
                idx = data[p] >> 4                               # ... or the high nibble
                rgb = palette[idx][:3] if idx < len(palette) else None
            elif bpp == 8:
                idx = data[p]
                rgb = palette[idx][:3] if idx < len(palette) else None
            elif bpp == 24:
                rgb = data[p:p + 3]
            else:
                m3 = (masks or [0x7C00, 0x03E0, 0x001F])[:3]
                if all(m3):
                    v = int.from_bytes(data[p:p + bpp // 8], "little")
                    r, g, b = (field(v, m) for m in m3)
                    rgb = bytes((b, g, r))
            if rgb is not None:
                b, g, r = rgb
                out["corner_pixel"] = "#%02x%02x%02x" % (r, g, b)
    return out
