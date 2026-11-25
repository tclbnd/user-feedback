"""X Window System screen dumps (.xwd, file version 7): header, colormap and pixels."""
import struct

FORMATS = {0: "xybitmap", 1: "xypixmap", 2: "zpixmap"}
VISUALS = {0: "static_gray", 1: "gray_scale", 2: "static_color", 3: "pseudo_color", 4: "true_color", 5: "direct_color"}


class XwdError(ValueError):
    pass


def scale(v, mask):
    if not mask:
        return 0
    shift = (mask & -mask).bit_length() - 1
    return (v & mask) >> shift


def parse(data):
    if len(data) < 100:
        raise XwdError("not an XWD file")
    h = struct.unpack_from(">25I", data, 0)
    (head_size, version, fmt, depth, width, height, xoff, byte_order, unit, bit_order, pad, bpp, bpl,
     visual, rmask, gmask, bmask, bits_rgb, cmap_entries, ncolors) = h[:20]
    if version != 7 or head_size < 100:
        raise XwdError("not an XWD version 7 file")
    if fmt != 2:
        raise XwdError("only ZPixmap images are supported")
    name = data[100:head_size].split(b"\0")[0].decode("latin-1") or None
    cmap = []
    p = 100
    for i in range(ncolors):
        pix, r, g, b = struct.unpack_from(">IHHH", data, p)
        cmap.append("#%02x%02x%02x" % (r & 0xFF, g & 0xFF, b & 0xFF))
        p += 12
    image = p
    need = bpl * height

    def pixel(x, y):
        row = image + y * (width * bpp // 8)
        if bpp in (8, 16, 24, 32):
            n = bpp // 8
            raw = data[row + x * n:row + x * n + n]
            return int.from_bytes(raw, "big")
        if bpp == 4:
            b = data[row + x // 2]
            return b >> 4 if x % 2 == 0 else b & 15
        if bpp == 1:
            b = data[row + x // 8]
            return b >> (7 - x % 8) & 1
        raise XwdError("unsupported bits per pixel %d" % bpp)

    def colour(x, y):
        v = pixel(x, y)
        if visual in (4, 5):
            return "#%02x%02x%02x" % (scale(v, rmask), scale(v, gmask), scale(v, bmask))
        return cmap[v] if v < len(cmap) else "#000000"

    complete = image + need <= len(data)
    pts = {"top_left": (0, 0), "top_right": (width - 1, 0), "center": (width // 2, height // 2),
           "bottom_right": (width - 1, height - 1)}
    return {"width": width, "height": height, "depth": depth, "bits_per_pixel": bpp,
            "byte_order": "msb" if byte_order == 1 else "lsb", "visual": VISUALS.get(visual, "visual_%d" % visual),
            "window_name": name, "colors": ncolors, "image_bytes": need, "complete": complete,
            "points": {k: colour(x, y) for k, (x, y) in pts.items()} if complete and width and height else None}
