"""Atari ST Spectrum 512 pictures (.spu): low-res bitplanes with 48 colours per scan line.

The file holds the 320 x 200 bitplane bitmap (32000 bytes) followed by 199
palette blocks, one per displayed scan line 1..199 (line 0 is shown with the
ST's all-black power-on palette, there is no palette block for it).  Each block
holds three consecutive 16-colour sets; the Spectrum 512 swaps the ST's 16
colour registers while the electron beam runs, so on every line a colour index
selects one of 48 colours depending on the column: columns 0..160 use set 0,
161..224 set 1 and 225..319 set 2.
"""
import struct

POINTS = {"line_0": (5, 0), "top_left": (0, 1), "x1": (1, 1), "x160": (160, 40), "x161": (161, 40),
          "center": (160, 100), "bottom_right": (319, 199)}

# First column of each of the three 16-colour sets loaded on a line.
SECTION_STARTS = (0, 161, 225)
# Line 0 has no palette block: the ST powers up with all 16 registers black.
BLACK_PALETTE = (0,) * 48


class SpuError(ValueError):
    pass


def index(data, x, y):
    """Colour index of pixel (x, y): four interleaved bitplanes per 16-pixel group, the high bit leftmost."""
    p = y * 160 + (x >> 4) * 8
    bit = 15 - (x & 15)
    return sum((struct.unpack_from(">H", data, p + 2 * pl)[0] >> bit & 1) << pl for pl in range(4))


def slot(x, c):
    """Which of the line's 48 colours index c shows at column x.

    The Spectrum 512 reloads the ST's 16 colour registers while the line is
    drawn: columns 0..160 use the first set, 161..224 the second and 225..319
    the third (the switch lands between pixels 160 and 161).
    """
    section = 0 if x < SECTION_STARTS[1] else (1 if x < SECTION_STARTS[2] else 2)
    return section * 16 + c


def widen(v):
    """Widen a 3-bit ST colour component to 8 bits: round(v * 255 / 7)."""
    return (v * 255 + 3) // 7


def rgb(word):
    return "#%02x%02x%02x" % (widen(word >> 8 & 7), widen(word >> 4 & 7), widen(word & 7))


def parse(data):
    if len(data) != 51104:
        raise SpuError("not a Spectrum 512 picture (%d bytes)" % len(data))
    # Palette block r (0-based) colours scan line r + 1; line 0 stays all black.
    palettes = [BLACK_PALETTE]
    palettes += [struct.unpack_from(">48H", data, 32000 + r * 96) for r in range(199)]

    def colour(x, y):
        return rgb(palettes[y][slot(x, index(data, x, y))])

    counts = [0] * 16
    for y in range(200):
        for x in range(320):
            counts[index(data, x, y)] += 1

    # Distinct ST colours actually displayed on lines 1..199: collect the
    # palette word every rendered pixel resolves to.
    colors_shown = set()
    for y in range(1, 200):
        pal = palettes[y]
        for x in range(320):
            colors_shown.add(pal[slot(x, index(data, x, y))])

    # Lines 2..199 whose 48-colour palette differs from the line above.
    palette_changes = sum(1 for y in range(2, 200) if palettes[y] != palettes[y - 1])

    return {"bytes": len(data), "points": {k: colour(x, y) for k, (x, y) in POINTS.items()},
            "index_counts": counts, "colors_shown": len(colors_shown),
            "palette_changes": palette_changes}
