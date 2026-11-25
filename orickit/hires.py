"""Oric HIRES screen dumps (.hir): 200 lines of 40 bytes.

Each line holds 40 cells of 6 pixels (240 pixels across).  Most cells are
graphics bytes: their six data bits, most significant bit first, switch the
pixel between the current INK (foreground) and PAPER (background) colours.

Two kinds of bytes are not graphics data but change how the following cells
of the same line are drawn; the video chip resets its colour state at the
start of every scan line:

* Standard HIRES attribute bytes (cells whose six-bit value is not a
  graphics byte, i.e. bit 6 of the low six bits is zero).  They fill their
  own cell with the new PAPER colour before taking effect.  Bits 2..0 hold
  the INK colour 0-7, bit 2 also flags inverse video, and the PAPER colour
  is the ROM PAPER encoding reversed: bit 2 stays put and the low two bits
  are swapped then shifted up, giving one of the four PAPER colours
  black/green/blue/cyan.
* Two-byte escape attributes used by dumped colour screens: 0x14 (normal)
  or 0x11 (inverse) followed by a bare INK colour number 0-7.  Both bytes
  are attribute cells filled with the current PAPER colour.
"""

WIDTH, HEIGHT, PER_LINE = 240, 200, 40

# Two-byte escape markers -> inverse flag (bit 2), mirroring the layout of
# the inverse bit in a standard attribute byte.
ESCAPE_MARKERS = {0x14: 0, 0x11: 1}


class HiresError(ValueError):
    pass


def _paper_colour(attr):
    """PAPER colour encoded in a standard HIRES attribute byte.

    The ROM stores a PAPER number ``p`` as ``0x40 | (p & 1) << 1 | p >> 1``;
    this reverses it, so only PAPER colours 0/2/4/6 are reachable.  e.g.
    0x02 -> blue (4), 0x40 -> black (0).
    """
    return (attr & 0x04) | ((attr & 0x03) << 1)


def _cell_colours(bits, fg, bg, inverse):
    """Colours of the six pixels of one graphics cell."""
    if inverse:
        return [bg if bit else fg for bit in bits]
    return [fg if bit else bg for bit in bits]


def decode_line(row):
    """Decode one 40-byte line.

    Returns ``(colours, attribute_bytes, inverse_cells)`` where ``colours``
    holds the colour (0-7) of each of the 240 displayed pixels, left to
    right, ``attribute_bytes`` counts bytes that are attributes rather than
    pixel data and ``inverse_cells`` counts graphics cells shown inverted.
    """
    # The chip starts every scan line in its reset state: white INK, black
    # PAPER, normal video.
    fg, bg, inverse = 7, 0, 0
    out = []
    attribute_bytes = 0
    inverse_cells = 0
    i = 0
    while i < PER_LINE:
        b = row[i]
        if b in ESCAPE_MARKERS and i + 1 < PER_LINE:
            # Two-byte escape: marker plus a bare INK colour number; both
            # cells are attribute cells filled with the current PAPER.
            inverse = ESCAPE_MARKERS[b]
            fg = row[i + 1]
            attribute_bytes += 2
            out.extend([bg] * 12)
            i += 2
            continue
        if b < 0x40 or b >= 0x80:
            # Standard HIRES attribute: bit 6 of the low six bits is zero.
            inverse = (b >> 2) & 1
            fg = b & 0x07
            bg = _paper_colour(b)
            attribute_bytes += 1
            out.extend([bg] * 6)
        else:
            bits = [(b >> (5 - k)) & 1 for k in range(6)]
            out.extend(_cell_colours(bits, fg, bg, inverse))
            if inverse:
                inverse_cells += 1
        i += 1
    return out, attribute_bytes, inverse_cells


def parse(data):
    if len(data) == 0 or len(data) > PER_LINE * HEIGHT:
        raise HiresError("not a HIRES screen")
    lines = len(data) // PER_LINE
    if lines == 0:
        raise HiresError("not a HIRES screen")
    hist = [0] * 8
    attribute_bytes = 0
    inverse_cells = 0
    first = last = None
    for y in range(lines):
        cols, line_attrs, line_inverse = decode_line(
            data[y * PER_LINE:(y + 1) * PER_LINE])
        attribute_bytes += line_attrs
        inverse_cells += line_inverse
        for c in cols:
            hist[c] += 1
        s = "".join(str(c) for c in cols)
        if y == 0:
            first = s
        last = s
    return {"lines": lines, "complete": len(data) == PER_LINE * HEIGHT,
            "histogram": hist,
            "colors_used": sum(1 for h in hist if h),
            "attribute_bytes": attribute_bytes,
            "inverse_cells": inverse_cells,
            "first_line": first, "last_line": last}
