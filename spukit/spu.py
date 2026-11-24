"""Atari ST Spectrum 512 pictures (.spu): low-res bitplanes with 48 colours per scan line."""
import struct

POINTS = {"line_0": (5, 0), "top_left": (0, 1), "x1": (1, 1), "x160": (160, 40), "x161": (161, 40),
          "center": (160, 100), "bottom_right": (319, 199)}


class SpuError(ValueError):
    pass


def index(data, x, y):
    """Colour index of pixel (x, y): four interleaved bitplanes per 16-pixel group, the high bit leftmost."""
    p = y * 160 + (x >> 4) * 8
    bit = 15 - (x & 15)
    return sum((struct.unpack_from(">H", data, p + 2 * pl)[0] >> bit & 1) << pl for pl in range(4))


def slot(x, c):
    """Which of the line's 48 colours index c shows at column x."""
    return c


def rgb(word):
    w = lambda v: (v & 7) * 32
    return "#%02x%02x%02x" % (w(word >> 8), w(word >> 4), w(word))


def parse(data):
    if len(data) != 51104:
        raise SpuError("not a Spectrum 512 picture (%d bytes)" % len(data))
    pal = [struct.unpack_from(">48H", data, 32000 + y * 96) for y in range(199)]

    def colour(x, y):
        return rgb(pal[min(y, 198)][slot(x, index(data, x, y))])

    counts = [0] * 16
    for y in range(200):
        for x in range(320):
            counts[index(data, x, y)] += 1
    return {"bytes": len(data), "points": {k: colour(x, y) for k, (x, y) in POINTS.items()},
            "index_counts": counts}
