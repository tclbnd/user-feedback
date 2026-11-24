"""Truevision TGA images: header, image ID, colour map, raw and RLE pixel data, origin, TGA 2.0 footer and extension."""
import struct
from fractions import Fraction

TYPES = {0: "none", 1: "colormapped", 2: "truecolor", 3: "grayscale", 9: "rle_colormapped", 10: "rle_truecolor",
         11: "rle_grayscale"}
ATTRIBUTES = ("no_alpha", "undefined_ignore", "undefined_retain", "alpha", "premultiplied_alpha")
SIGNATURE = b"TRUEVISION-XFILE.\0"


class TgaError(ValueError):
    pass


def text(raw):
    return raw.split(b"\0")[0].decode("latin-1").strip() or None


def widen5(v):
    """Widen a 5-bit channel to 8 bits by repeating the top bits."""
    return (v << 3) | (v >> 2)


def rgba(px, bits, alpha):
    """(r, g, b, a) of one stored pixel or colour map entry (little-endian BGR[A] or 5-5-5 with an attribute bit)."""
    if bits == 24:
        return px[2], px[1], px[0], 255
    if bits == 32:
        return px[2], px[1], px[0], px[3] if alpha else 255
    if bits in (15, 16):
        v = px[0] | px[1] << 8
        a = (255 if v & 0x8000 else 0) if bits == 16 and alpha else 255
        return widen5((v >> 10) & 0x1F), widen5((v >> 5) & 0x1F), widen5(v & 0x1F), a
    raise TgaError("unsupported pixel size %d" % bits)


def read_extension(data, off):
    """The 495-byte TGA 2.0 extension area at ``off``."""
    e = data[off:off + 495]
    lines = [text(e[43 + i * 81:43 + (i + 1) * 81]) for i in range(4)]
    stamp = struct.unpack_from("<6H", e, 367)          # month, day, year, hour, minute, second
    timestamp = ("%04d-%02d-%02dT%02d:%02d:%02d" % (stamp[2], stamp[0], stamp[1], stamp[3], stamp[4], stamp[5])
                 if any(stamp) else None)
    vnum, vletter = struct.unpack_from("<HB", e, 467)  # version number x100 plus a letter
    letter = chr(vletter) if 32 < vletter < 127 else ""
    pa_num, pa_den = struct.unpack_from("<2H", e, 474)
    g_num, g_den = struct.unpack_from("<2H", e, 478)
    thumb_off, = struct.unpack_from("<I", e, 486)
    thumbnail = [data[thumb_off], data[thumb_off + 1]] if thumb_off and thumb_off + 2 <= len(data) else None
    attr = e[494]
    return {"author": text(e[2:43]), "comments": "\n".join(line for line in lines if line) or None,
            "timestamp": timestamp, "job": text(e[379:420]), "software": text(e[426:467]),
            "software_version": "%d.%02d%s" % (vnum // 100, vnum % 100, letter) if vnum or letter else None,
            "pixel_aspect": str(Fraction(pa_num, pa_den)) if pa_den else None,
            "gamma": str(Fraction(g_num, g_den)) if g_den else None,
            "attribute_type": ATTRIBUTES[attr] if attr < len(ATTRIBUTES) else None, "thumbnail": thumbnail}


def parse(data):
    if len(data) < 18:
        raise TgaError("file shorter than a TGA header")
    idlen, cmtype, itype, cmfirst, cmlen, cmbits, xo, yo, w, h, depth, desc = struct.unpack_from("<BBBHHBHHHHBB", data)
    if itype not in TYPES:
        raise TgaError("unknown image type %d" % itype)
    p = 18
    image_id = text(data[p:p + idlen]) if idlen else None
    p += idlen
    cmap = []
    if cmtype == 1:
        eb = (cmbits + 7) // 8                          # bytes per colour map entry
        cmap = [data[p + i * eb:p + (i + 1) * eb] for i in range(cmlen)]
        p += cmlen * eb
    bpp = (depth + 7) // 8
    total = w * h
    pixels, packets, crossing = [], 0, 0
    rle = itype >= 9
    while len(pixels) < total and p < len(data):
        if rle:
            head = data[p]
            p += 1
            count = (head & 0x7F) + 1                   # repetition count field is stored minus one
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
    right, top = bool(desc & 0x10), bool(desc & 0x20)   # descriptor bits 4 and 5

    version, ext = 1, None
    if len(data) >= 26 and data[-18:] == SIGNATURE:
        version = 2
        off, = struct.unpack_from("<I", data, len(data) - 26)
        if off and off + 495 <= len(data) and data[off:off + 2] == struct.pack("<H", 495):
            ext = read_extension(data, off)

    has_alpha = alpha_bits > 0
    if ext and ext["attribute_type"] in ("no_alpha", "undefined_ignore", "undefined_retain"):
        has_alpha = False                               # the file says its attribute bits are not alpha

    corner = None
    if pixels and len(pixels) == total and itype:
        # the pixel shown at the top-left corner of the picture
        row = 0 if top else h - 1
        col = w - 1 if right else 0
        px = pixels[row * w + col]
        if itype in (1, 9):                             # colour-mapped: the pixel indexes the map
            idx = int.from_bytes(px, "little") - cmfirst
            if 0 <= idx < len(cmap) and len(cmap[idx]) == (cmbits + 7) // 8:
                corner = "#%02x%02x%02x%02x" % rgba(cmap[idx], cmbits, has_alpha)
        elif itype in (2, 10):                          # truecolor: 15/16/24/32 bits per pixel
            corner = "#%02x%02x%02x%02x" % rgba(px, depth, has_alpha)
        else:                                           # grayscale: 8 bits, or 8 plus 8 of alpha
            if depth not in (8, 16):
                raise TgaError("unsupported pixel size %d" % depth)
            a = px[1] if depth == 16 and has_alpha else 255
            corner = "#%02x%02x%02x%02x" % (px[0], px[0], px[0], a)
    return {"type": TYPES[itype], "width": w, "height": h, "depth": depth, "image_id": image_id,
            "colormap": {"first": cmfirst, "length": cmlen, "entry_bits": cmbits} if cmtype == 1 else None,
            "origin": ("top" if top else "bottom") + "_" + ("right" if right else "left"), "alpha_bits": alpha_bits,
            "pixels": len(pixels), "complete": len(pixels) == total, "rle_packets": packets if rle else None,
            "crossing_packets": crossing if rle else None, "corner_pixel": corner, "version": version,
            "extension": ext}
