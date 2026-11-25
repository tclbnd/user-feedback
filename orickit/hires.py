"""Oric HIRES screen dumps (.hir): 200 lines of 40 bytes from $A000.

Each byte is one 6-pixel cell.  The Oric video chip (HCS10017) treats a
byte in HIRES mode as either pixel data or an attribute:

* bit 6 clear -> graphics byte.  Bits 5..0 are the six pixels, MSB left;
  bit 7 is unused.  A set pixel is drawn in the current ink colour, a
  clear one in the current paper colour.
* bit 6 set -> attribute byte.  It occupies a cell but draws no pixels;
  the whole cell is filled with the paper colour.  The low six bits hold
  two 3-bit colour nibbles (Oric numbering 0 black .. 7 white):

      bits 2..0  nibble A
      bits 5..3  nibble B
      bit 7     inverse/alternate flag

  With bit 7 clear, A is paper and B is ink.  With bit 7 set, the two are
  swapped (A = ink, B = paper) and every following graphics cell is shown
  inverted: set pixels take paper and clear pixels take ink, until another
  attribute changes the state.

Colour and inverse state latch at the start of the frame with ink = white
(7) and paper = black (0), then persist from cell to cell across line
boundaries for the rest of the screen.
"""

WIDTH, HEIGHT, PER_LINE = 240, 200, 40
CELL_PIXELS = 6

DEFAULT_INK, DEFAULT_PAPER = 7, 0


class HiresError(ValueError):
    pass


def _is_attribute(byte):
    return bool(byte & 0x40)


def parse(data):
    if len(data) == 0 or len(data) > PER_LINE * HEIGHT:
        raise HiresError("not a HIRES screen")
    lines = len(data) // PER_LINE
    if lines == 0:
        raise HiresError("not a HIRES screen")

    ink, paper = DEFAULT_INK, DEFAULT_PAPER
    inverse = False
    hist = [0] * 8
    attribute_bytes = 0
    inverse_cells = 0
    first = last = None

    for y in range(lines):
        cols = []
        base = y * PER_LINE
        for x in range(PER_LINE):
            byte = data[base + x]
            if _is_attribute(byte):
                attribute_bytes += 1
                nibble_a = byte & 0x07
                nibble_b = (byte >> 3) & 0x07
                inverse = bool(byte & 0x80)
                if inverse:
                    ink, paper = nibble_a, nibble_b
                else:
                    paper, ink = nibble_a, nibble_b
                # An attribute cell paints its full width in paper colour.
                cols.extend([paper] * CELL_PIXELS)
            else:
                if inverse:
                    inverse_cells += 1
                    on_colour, off_colour = paper, ink
                else:
                    on_colour, off_colour = ink, paper
                for bit in range(CELL_PIXELS):
                    set_pixel = (byte >> (CELL_PIXELS - 1 - bit)) & 1
                    cols.append(on_colour if set_pixel else off_colour)

        for colour in cols:
            hist[colour] += 1
        line = "".join(str(colour) for colour in cols)
        if y == 0:
            first = line
        last = line

    return {
        "lines": lines,
        "complete": len(data) == PER_LINE * HEIGHT,
        "histogram": hist,
        "colors_used": sum(1 for h in hist if h),
        "attribute_bytes": attribute_bytes,
        "inverse_cells": inverse_cells,
        "first_line": first,
        "last_line": last,
    }
