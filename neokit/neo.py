"""Atari ST NEOchrome pictures (.neo): header, ST/STE palette, colour cycling, interleaved bitplanes."""
import struct

POINTS = {"top_left": (0, 0), "x15": (15, 0), "x16": (16, 0), "center": (160, 100), "bottom_right": (319, 199)}

# resolution word: (name, width, height, bitplanes)
RESOLUTIONS = {
    0: ("low", 320, 200, 4),
    1: ("medium", 640, 200, 2),
    2: ("high", 640, 400, 1),
}


class NeoError(ValueError):
    pass


def ste(v):
    """ST/STE palette word: per component, bit 3 is the STE extra low bit, bits 0-2 the ST bits."""
    def comp(n):
        return (((n & 7) << 1) | (n >> 3 & 1)) * 17
    return "#%02x%02x%02x" % (comp(v >> 8 & 15), comp(v >> 4 & 15), comp(v & 15))


def pixel(screen, res, x, y):
    """Colour index at (x, y).  Bitplanes are interleaved in 16-pixel groups
    (one word per plane per group); the highest bit of a word is the leftmost pixel."""
    planes = RESOLUTIONS[res][3]
    groups = RESOLUTIONS[res][1] // 16  # 16-pixel groups per scanline
    offset = (y * groups + x // 16) * planes * 2
    bit = 15 - x % 16
    v = 0
    for pl in range(planes):
        w, = struct.unpack_from(">H", screen, offset + 2 * pl)
        v |= (w >> bit & 1) << pl
    return v


def parse(data):
    if len(data) < 128 + 32000:
        raise NeoError("not a NEOchrome picture")
    flag, res = struct.unpack_from(">HH", data, 0)
    if flag != 0:
        raise NeoError("not a NEOchrome picture")
    if res not in RESOLUTIONS:
        raise NeoError("unknown resolution %d" % res)
    name, w, h, planes = RESOLUTIONS[res]
    pal = struct.unpack_from(">16H", data, 4)
    filename = data[36:48].decode("latin-1").rstrip("\0 ").strip() or None
    limits, speed, steps = struct.unpack_from(">HHH", data, 48)
    if limits & 0x8000:
        cycling = {"left": limits >> 4 & 15, "right": limits & 15,
                   "direction": "right" if speed & 0x8000 else "left",
                   "delay": speed & 0xFF, "steps": steps}
    else:
        cycling = None
    xo, yo = struct.unpack_from(">HH", data, 54)
    screen = data[128:128 + 32000]
    pts = {}
    for k, (x, y) in POINTS.items():
        xx, yy = min(x, w - 1), min(y, h - 1)
        if k == "bottom_right":
            xx, yy = w - 1, h - 1
        idx = pixel(screen, res, xx, yy)
        pts[k] = {"index": idx, "color": ste(pal[idx]) if res < 2 else ("#ffffff" if idx == 0 else "#000000")}
    return {"resolution": name, "width": w, "height": h, "filename": filename,
            "palette": [ste(v) for v in pal[:1 << planes]], "cycling": cycling,
            "offset": [xo, yo], "points": pts}
