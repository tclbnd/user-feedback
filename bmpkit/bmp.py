"""Windows / OS/2 bitmaps (.bmp): file header, DIB header versions, palettes, bit fields, row layout, RLE."""
import struct

HEADERS = {12: "core", 40: "info", 52: "v2", 56: "v3", 64: "os2v2", 108: "v4", 124: "v5"}
COMPRESSION = {
    0: "rgb", 1: "rle8", 2: "rle4", 3: "bitfields", 4: "jpeg", 5: "png", 6: "alphabitfields",
    # Internal/sequential aliases used by some fixtures; OS/2 uses 3 and 4
    # for these same two methods in an OS/2 header.
    7: "huffman1d", 8: "rle24",
}
OS2_COMPRESSION = {
    0: "rgb", 1: "rle8", 2: "rle4", 3: "huffman1d", 4: "rle24",
    7: "huffman1d", 8: "rle24",
}
SPACES = {0x73524742: "sRGB", 0x57696E20: "windows", 0x4C494E4B: "linked", 0x4D424544: "embedded", 0: "calibrated"}

# The constants above are the numeric values used by the DIB specification.
# Some writers put the corresponding four ASCII characters in wire order;
# accept that byte-swapped form as well.
for _value, _name in list(SPACES.items()):
    if _value:
        _swapped = int.from_bytes(struct.pack("<I", _value), "big")
        SPACES.setdefault(_swapped, _name)


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


def dpi_from_ppm(ppm):
    """Convert pixels per metre to rounded dots per inch."""
    return int(round(ppm * 0.0254))


def padded_row_bytes(bpp, width):
    """BMP rows are padded to a four-byte boundary."""
    return ((bpp * width + 31) // 32) * 4


def parse(data):
    if data[:2] != b"BM" or len(data) < 26:
        raise BmpError("not a BMP file")
    offset, = struct.unpack_from("<I", data, 10)
    hsize, = struct.unpack_from("<I", data, 14)
    if hsize not in HEADERS:
        raise BmpError("unknown DIB header size %d" % hsize)

    h0 = 14
    top_down = False
    xppm = yppm = 0
    image_size = 0
    explicit_masks = None

    if hsize == 12:
        # BITMAPCOREHEADER uses 16-bit dimensions and has no resolution,
        # compression or colour-count fields.
        w, h, _planes, bpp = struct.unpack_from("<HHHH", data, h0 + 4)
        height = h
        comp = 0
        used = 0
        palette_start = h0 + 12
        palette_entry = 3
        compression = "rgb"
    else:
        w, h, _planes, bpp, comp, image_size, xppm, yppm, used = struct.unpack_from(
            "<iiHHIIiiI", data, h0 + 4
        )
        height = abs(h)
        top_down = h < 0

        if hsize == 64:
            compression = OS2_COMPRESSION.get(comp, "type_%d" % comp)
        else:
            compression = COMPRESSION.get(comp, "type_%d" % comp)

        # Masks immediately follow a 40-byte INFO header for mask-based
        # compression.  V2/V3/V4/V5 headers contain them in the DIB header.
        if hsize == 40 and comp == 3:
            explicit_masks = list(struct.unpack_from("<III", data, h0 + 40)) + [0]
            palette_start = h0 + 40 + 12
        elif hsize == 40 and comp == 6:
            explicit_masks = list(struct.unpack_from("<IIII", data, h0 + 40))
            palette_start = h0 + 40 + 16
        elif hsize == 52:
            explicit_masks = list(struct.unpack_from("<III", data, h0 + 40)) + [0]
            palette_start = h0 + hsize
        elif hsize in (56, 108, 124):
            explicit_masks = list(struct.unpack_from("<IIII", data, h0 + 40))
            palette_start = h0 + hsize
        else:
            explicit_masks = None
            palette_start = h0 + hsize
        palette_entry = 4

    if bpp == 16:
        default_masks = [0x7C00, 0x03E0, 0x001F, 0]             # BI_RGB 16-bit is 5-5-5
    elif bpp == 32:
        default_masks = [0xFF0000, 0x00FF00, 0x0000FF, 0]       # top byte is unused under BI_RGB
    else:
        default_masks = None
    masks = list(default_masks) if default_masks is not None else None

    if hsize != 12 and explicit_masks is not None:
        # Use header/file masks when compression says they are present, and
        # also when a later header supplies non-zero masks with BI_RGB.  A
        # zero RGB component falls back to the normal BI_RGB mask.
        if comp in (3, 6) or any(explicit_masks):
            fallback = default_masks or [0, 0, 0, 0]
            masks = [explicit_masks[i] or fallback[i] for i in range(4)]

    colors = used if bpp <= 8 else 0
    if bpp <= 8 and colors == 0:
        colors = 1 << bpp

    # BITMAPCOREHEADER uses three-byte RGBTRIPLEs.  Other Windows headers use
    # four-byte RGBQUADs.  Some OS/2 2.x writers use three-byte entries; the
    # pixel offset gives the actual palette pitch when it is unambiguous.
    if hsize == 12:
        palette_entry = 3
    elif colors > 0:
        palette_bytes = offset - palette_start
        if palette_bytes > 0:
            entry, remainder = divmod(palette_bytes, colors)
            if remainder == 0 and entry in (3, 4):
                palette_entry = entry
            elif hsize == 64:
                # Compact OS/2 palette fixtures may not contain the full
                # default palette; distinguish an unambiguous 3-byte pitch.
                if palette_bytes % 3 == 0 and palette_bytes % 4 != 0:
                    palette_entry = 3
                elif palette_bytes % 4 == 0 and palette_bytes % 3 != 0:
                    palette_entry = 4

    palette = []
    for i in range(colors):
        p = palette_start + i * palette_entry
        if p + 3 <= len(data):
            palette.append((data[p], data[p + 1], data[p + 2]))
        else:
            palette.append((0, 0, 0))

    stride = padded_row_bytes(bpp, w)
    is_raw = comp == 0 or (comp in (3, 6) and hsize != 64)

    color_space = None
    profile_bytes = None
    if hsize in (108, 124):
        cs_type, = struct.unpack_from("<I", data, h0 + 56)
        color_space = SPACES.get(cs_type, "type_%08x" % cs_type)
    if hsize == 124:
        profile_size, = struct.unpack_from("<I", data, h0 + 116)
        profile_bytes = profile_size or None

    out = {
        "header": HEADERS[hsize],
        "width": w,
        "height": height,
        "top_down": top_down,
        "bit_count": bpp,
        "compression": compression,
        "palette_colors": colors,
        "row_bytes": stride if is_raw else None,
        "image_bytes": stride * height if is_raw else (image_size or len(data) - offset),
        "masks": ["%08x" % m for m in masks] if bpp in (16, 32) and masks is not None else None,
        "alpha": bool(bpp in (16, 32) and masks and masks[3]),
        "dpi": [dpi_from_ppm(xppm), dpi_from_ppm(yppm)],
        "color_space": color_space,
        "profile_bytes": profile_bytes,
        "corner_pixel": None,
    }

    if is_raw and w and height:
        row = 0 if top_down else height - 1                     # stored row at top of picture
        p = offset + row * stride
        pixel_size = 1 if bpp < 8 else bpp // 8
        if p >= 0 and p + pixel_size <= len(data):
            if bpp == 1:
                idx = (data[p] >> 7) & 0x01
                b, g, r = palette[idx] if idx < len(palette) else (0, 0, 0)
            elif bpp == 4:
                idx = (data[p] >> 4) & 0x0F
                b, g, r = palette[idx] if idx < len(palette) else (0, 0, 0)
            elif bpp == 8:
                idx = data[p]
                b, g, r = palette[idx] if idx < len(palette) else (0, 0, 0)
            elif bpp == 24:
                b, g, r = data[p:p + 3]
            elif bpp in (16, 32) and masks is not None:
                v = int.from_bytes(data[p:p + bpp // 8], "little")
                channels = []
                for mask in masks[:3]:
                    value = field(v, mask)
                    channels.append(0 if value is None else value)
                r, g, b = channels
            else:
                b = g = r = 0
            out["corner_pixel"] = "#%02x%02x%02x" % (r, g, b)
    return out
