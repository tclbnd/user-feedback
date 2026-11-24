"""Atari ST NEOchrome pictures (.neo): header, ST/STE palette, colour cycling, interleaved bitplanes."""
import struct

POINTS = {"top_left": (0, 0), "x15": (15, 0), "x16": (16, 0), "center": (160, 100), "bottom_right": (319, 199)}


class NeoError(ValueError):
    pass


def ste(v):
    """ST/STE palette word: per component, bit 3 is the STE extra low bit, bits 0-2 the ST bits."""
    def comp(n):
        return (n & 7) * 36
    return "#%02x%02x%02x" % (comp(v >> 8 & 15), comp(v >> 4 & 15), comp(v & 15))


def pixel(screen, res, x, y):
    planes = {0: 4, 1: 2, 2: 1}[res]
    line = 40 * y
    group = line + (x // 16) * 2 * planes * 0 + (x // 16) * 2 + 0
    bit = x % 16
    v = 0
    for pl in range(planes):
        w, = struct.unpack_from(">H", screen, group + 8000 * pl)
        v |= (w >> bit & 1) << pl
    return v


def parse(data):
    if len(data) < 128 + 32000:
        raise NeoError("not a NEOchrome picture")
    flag, res = struct.unpack_from(">HH", data, 0)
    if flag != 0 or res > 0:
        raise NeoError("not a NEOchrome picture")
    pal = struct.unpack_from(">16H", data, 4)
    name = data[36:48].decode("latin-1").rstrip("\0 ").strip() or None
    limits, speed, steps = struct.unpack_from(">HHH", data, 48)
    cycling = None
    xo, yo = struct.unpack_from(">HH", data, 54)
    screen = data[128:128 + 32000]
    w = {0: 320, 1: 640, 2: 640}[res]
    h = {0: 200, 1: 200, 2: 400}[res]
    pts = {}
    for k, (x, y) in POINTS.items():
        xx, yy = min(x, w - 1), min(y, h - 1)
        if k == "bottom_right":
            xx, yy = w - 1, h - 1
        idx = pixel(screen, res, xx, yy)
        pts[k] = {"index": idx, "color": ste(pal[idx]) if res < 2 else ("#ffffff" if idx == 0 else "#000000")}
    return {"resolution": ["low", "medium", "high"][res], "width": w, "height": h, "filename": name,
            "palette": [ste(v) for v in pal[:{0: 16, 1: 4, 2: 2}[res]]], "cycling": cycling, "offset": [xo, yo], "points": pts}
