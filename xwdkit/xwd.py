"""X Window System screen dumps (.xwd, file version 7): header, colormap and pixels."""
import struct

FORMATS = {0: "xybitmap", 1: "xypixmap", 2: "zpixmap"}
VISUALS = {0: "static_gray", 1: "gray_scale", 2: "static_color", 3: "pseudo_color", 4: "true_color", 5: "direct_color"}


class XwdError(ValueError):
    pass


def scale(v, mask):
    """Widen the component of ``v`` selected by ``mask`` to 8 bits."""
    if not mask:
        return 0
    shift = (mask & -mask).bit_length() - 1
    value = (v & mask) >> shift
    top = mask >> shift
    return (value * 255 + top // 2) // top


def _header_order(data):
    """XWD headers are big-endian, but dumps from little-endian hosts are common."""
    if len(data) < 100:
        raise XwdError("not an XWD file")
    for order in (">", "<"):
        head_size, version = struct.unpack_from(order + "2I", data, 0)
        if version == 7 and head_size >= 100:
            return order
    raise XwdError("not an XWD version 7 file")


def parse(data):
    order = _header_order(data)
    h = struct.unpack_from(order + "25I", data, 0)
    (head_size, version, fmt, depth, width, height, xoff, byte_order, unit, bit_order, pad, bpp, bpl,
     visual, rmask, gmask, bmask, bits_rgb, cmap_entries, ncolors) = h[:20]
    if fmt != 2:
        raise XwdError("only ZPixmap images are supported")
    if head_size > len(data):
        raise XwdError("truncated XWD header")
    if not 0 < bpp <= 32:
        raise XwdError("unsupported bits per pixel %d" % bpp)
    if bpp >= 8 and bpp % 8:
        raise XwdError("unsupported bits per pixel %d" % bpp)
    if width and bpl * 8 < width * bpp:
        raise XwdError("bytes per line %d is too small for %d pixels at %d bits" % (bpl, width, bpp))
    # The window name, when present, is NUL-terminated and padded out to header_size.
    name = data[100:head_size].split(b"\0")[0].decode("latin-1") or None
    # The colour table starts right after the header (not after the window name's first byte).
    cmap = {}
    p = head_size
    if p + 12 * ncolors > len(data):
        raise XwdError("truncated colormap")
    for _ in range(ncolors):
        pix, r, g, b = struct.unpack_from(order + "IHHH", data, p)
        cmap[pix] = "#%02x%02x%02x" % ((r >> 8) & 0xFF, (g >> 8) & 0xFF, (b >> 8) & 0xFF)
        p += 12
    image = p
    need = bpl * height

    def pixel(x, y):
        # Rows are padded to bytes_per_line, which need not be width * bits_per_pixel / 8.
        row = image + y * bpl
        if bpp >= 8:
            n = bpp // 8
            off = row + x * n
            return int.from_bytes(data[off:off + n], "big" if byte_order == 1 else "little")
        start = x * bpp
        value = 0
        for i in range(bpp):
            bit = start + i
            byte = data[row + bit // 8]
            if bit_order == 1:      # MSBFirst: the high bit of a byte holds the first pixel
                value = (value << 1) | ((byte >> (7 - bit % 8)) & 1)
            else:                   # LSBFirst: the low bit holds the first pixel
                value |= ((byte >> (bit % 8)) & 1) << i
        return value

    def colour(x, y):
        v = pixel(x, y)
        if visual in (4, 5):
            return "#%02x%02x%02x" % (scale(v, rmask), scale(v, gmask), scale(v, bmask))
        return cmap.get(v, "#000000")

    complete = image + need <= len(data)
    pts = {"top_left": (0, 0), "top_right": (width - 1, 0), "center": (width // 2, height // 2),
           "bottom_right": (width - 1, height - 1)}
    return {"width": width, "height": height, "depth": depth, "bits_per_pixel": bpp,
            "byte_order": "msb" if byte_order == 1 else "lsb", "visual": VISUALS.get(visual, "visual_%d" % visual),
            "window_name": name, "colors": ncolors, "image_bytes": need, "complete": complete,
            "points": {k: colour(x, y) for k, (x, y) in pts.items()} if complete and width and height else None}
