"""Atari ST Spectrum 512 pictures (.spu): low-res bitplanes with 48 colours per scan line."""
import struct

WIDTH, HEIGHT = 320, 200
LINE_BYTES = 160
PALETTE_OFFSET = 32000
PALETTE_BYTES = 96
PALETTE_LINES = 199

POINTS = {"line_0": (5, 0), "top_left": (0, 1), "x1": (1, 1), "x160": (160, 40), "x161": (161, 40),
          "center": (160, 100), "bottom_right": (319, 199)}


class SpuError(ValueError):
    pass


def index(data, x, y):
    """Colour index of pixel (x, y): four interleaved bitplanes per 16-pixel group, the high bit leftmost."""
    p = y * LINE_BYTES + (x >> 4) * 8
    bit = 15 - (x & 15)
    return sum((struct.unpack_from(">H", data, p + 2 * pl)[0] >> bit & 1) << pl for pl in range(4))


def slot(x, c):
    """Position of colour index c in the line's 48-colour palette.

    The viewer reloads the 16 palette registers twice during a scan line, so a
    line shows its three 16-colour sets on the first 160 pixels, the next 80 and
    the last 80 (x 0-159, 160-239 and 240-319).
    """
    return c + 16 * (0 if x < 160 else 1 if x < 240 else 2)


def component(v):
    """One ST colour component (3 bits) widened to 8 bits."""
    return round((v & 7) * 255 / 7)


def rgb(word):
    """`#rrggbb` for a 12-bit ST colour register value."""
    return "#%02x%02x%02x" % (component(word >> 8), component(word >> 4), component(word))


def parse(data):
    if len(data) != 51104:
        raise SpuError("not a Spectrum 512 picture (%d bytes)" % len(data))
    # The stored palettes are the ones loaded during lines 0-198, so each is
    # shown on the following line.  Line 0 is displayed before any palette has
    # been loaded, so it stays black.
    palettes = [(0,) * 48]
    palettes += [struct.unpack_from(">48H", data, PALETTE_OFFSET + y * PALETTE_BYTES)
                 for y in range(PALETTE_LINES)]

    counts = [0] * 16
    shown = set()
    for y in range(HEIGHT):
        for x in range(WIDTH):
            c = index(data, x, y)
            counts[c] += 1
            if y:
                shown.add(palettes[y][slot(x, c)] & 0x777)
    changes = sum(1 for y in range(2, HEIGHT) if palettes[y] != palettes[y - 1])

    return {"bytes": len(data),
            "points": {name: rgb(palettes[y][slot(x, index(data, x, y))])
                       for name, (x, y) in POINTS.items()},
            "index_counts": counts, "colors_shown": len(shown), "palette_changes": changes}
