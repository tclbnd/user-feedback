"""Truevision TGA images: header, image ID, colour map, raw and RLE pixel data, origin, TGA 2.0 footer and extension."""
import struct
from fractions import Fraction

TYPES = {0: "none", 1: "colormapped", 2: "truecolor", 3: "grayscale", 9: "rle_colormapped", 10: "rle_truecolor",
         11: "rle_grayscale"}
ATTRIBUTES = ("no_alpha", "undefined_ignore", "undefined_retain", "alpha", "premultiplied_alpha")
SIGNATURE = b"TRUEVISION-XFILE\0"


class TgaError(ValueError):
    pass


def text(raw):
    return raw.split(b"\0")[0].decode("latin-1").strip() or None


def rgba(px, bits, alpha_bits):
    """(r, g, b, a) of one stored pixel (little-endian BGR[A] or 5-5-5 with an attribute bit)."""
    if bits in (15, 16):
        v = px[0] | px[1] << 8
        ex = lambda x: x << 3 | x >> 2
        a = (255 if v >> 15 & 1 else 0) if bits == 16 and alpha_bits else 255
        return ex(v >> 10 & 31), ex(v >> 5 & 31), ex(v & 31), a
    if bits == 24:
        return px[2], px[1], px[0], 255
    if bits == 32:
        return px[2], px[1], px[0], px[3] if alpha_bits else 255
    raise TgaError("unsupported pixel size %d" % bits)


def parse(data):
    if len(data) < 18:
        raise TgaError("file shorter than a TGA header")
    idlen, cmtype, itype, cmfirst, cmlen, cmbits, xo, yo, w, h, depth, desc = struct.unpack_from("<BBBHHBHHHHBB", data)
    if itype not in TYPES:
        raise TgaError("unknown image type %d" % itype)
    p = 18
    image_id = None
    cmap = []
    if cmtype == 1:
        eb = cmbits
        for i in range(cmlen):
            cmap.append(rgba(data[p + i * eb:p + (i + 1) * eb], cmbits, 0 if cmbits != 32 else 8))
        p += cmlen * (cmbits + 7) // 8 * 8
    bpp = (depth + 7) // 8
    total = w * h
    pixels, packets, crossing = [], 0, 0
    rle = itype >= 9
    start = p
    while len(pixels) < total and p < len(data):
        if rle:
            head = data[p]
            p += 1
            count = head & 0x7F
            packets += 1
            col = len(pixels) % w if w else 0
            if w and col + count > w:
                crossing += 1                                  # a packet running into the next scan line
            if head & 0x80:
                pixels.extend([data[p:p + bpp]] * count)
                p += bpp
            else:
                for _ in range(count):
                    pixels.append(data[p:p + bpp])
                    p += bpp
        else:
            pixels.append(data[p:p + bpp])
            p += bpp
    pixels = [px for px in pixels if len(px) == bpp][:total]
    alpha_bits = desc & 0x0F
    right, top = False, bool(desc & 0x10)
    corner = None
    if pixels and len(pixels) == total and itype:
        # the pixel shown at the top-left corner of the picture
        row = 0 if top else h - 1
        col = w - 1 if right else 0
        px = pixels[row * w + col]
        if itype in (1, 9):
            r, g, b, a = cmap[(px[0] | (px[1] << 8 if bpp > 1 else 0)) - cmfirst]
        elif itype in (3, 11):
            r = g = b = px[0]
            a = px[1] if bpp > 1 else 255
        else:
            r, g, b, a = rgba(px, depth, alpha_bits)
        corner = "#%02x%02x%02x%02x" % (r, g, b, a)
    out = {"type": TYPES[itype], "width": w, "height": h, "depth": depth, "image_id": image_id,
           "colormap": {"first": cmfirst, "length": cmlen, "entry_bits": cmbits} if cmtype == 1 else None,
           "origin": ("top" if top else "bottom") + "_" + ("right" if right else "left"), "alpha_bits": alpha_bits,
           "pixels": len(pixels), "complete": len(pixels) == total, "rle_packets": packets if rle else None,
           "crossing_packets": crossing if rle else None, "corner_pixel": corner, "version": 1, "extension": None}
    if len(data) >= 26 and data[-17:] == SIGNATURE:
        out["version"] = 2
        ext, = struct.unpack_from("<I", data, len(data) - 26)
        if ext and data[ext:ext + 2] == struct.pack("<H", 495):
            e = data[ext:ext + 495]
            mo, d, y, hh, mi, ss = struct.unpack_from("<6H", e, 367)
            an, ad, gn, gd = struct.unpack_from("<4H", e, 474)
            out["extension"] = {"author": text(e[2:43]), "comments": text(e[43:367]),
                                "timestamp": "%04d-%02d-%02dT%02d:%02d:%02d" % (y, mo, d, hh, mi, ss) if y else None,
                                "job": text(e[379:420]), "software": text(e[426:467]),
                                "pixel_aspect": str(Fraction(an, ad)) if ad else None,
                                "gamma": str(Fraction(gn, gd)) if gd else None,
                                "attribute_type": ATTRIBUTES[e[494]] if e[494] < 5 else None}
    return out
