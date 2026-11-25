"""Commodore 128 VDC snapshots (.vdc): the 80-column text screen."""

NREG = 37

# Register 25 bit 6 turns per-cell attributes on; otherwise the screen is
# plain text and every cell uses the global foreground colour.
R25_ATTRIBUTES = 0x40

# Per-cell attribute bits (attribute mode only).
ATTR_ALTERNATE = 0x80
ATTR_REVERSE = 0x40
ATTR_UNDERLINE = 0x20
ATTR_BLINK = 0x10
ATTR_COLOR = 0x0F

CHARSET_CHARS = 256          # characters per character set
TOTAL_CHARS = 512            # both character sets in the character memory
GLYPH_BYTES = 16


class VdcError(ValueError):
    pass


def parse(data):
    if len(data) < 8 + NREG or data[:8] != b"VDCSNAP\x00":
        raise VdcError("not a VDC snapshot")
    r = data[8:8 + NREG]
    cols, rows = r[1], r[6]
    # R9 holds the character height in scan lines, minus one (0-15 -> 1-16).
    h = (r[9] & 0x0F) + 1
    bg = r[26] & 0x0F
    text_fg = (r[26] >> 4) & 0x0F
    use_attrs = bool(r[25] & R25_ATTRIBUTES)

    n = cols * rows
    base = 8 + NREG
    need = base + 2 * n + TOTAL_CHARS * GLYPH_BYTES
    if len(data) < need:
        raise VdcError("snapshot is truncated")
    scr = data[base:base + n]
    att = data[base + n:base + 2 * n]
    gen = data[base + 2 * n:need]

    # Resolve each cell's ink and paper colours first; scan lines then read
    # row by row so that the top pixel line spans the whole screen width.
    ink = [0] * n
    paper = [0] * n
    underneath = [False] * n
    codes = list(scr)
    reverse_cells = underline_cells = blink_cells = alternate_cells = 0
    for cell in range(n):
        fg, rev, underline = text_fg, False, False
        if use_attrs:
            a = att[cell]
            fg = a & ATTR_COLOR
            rev = bool(a & ATTR_REVERSE)
            underline = bool(a & ATTR_UNDERLINE)
            if rev:
                reverse_cells += 1
            if underline:
                underline_cells += 1
            if a & ATTR_BLINK:
                blink_cells += 1
            if a & ATTR_ALTERNATE:
                alternate_cells += 1
                codes[cell] = scr[cell] + CHARSET_CHARS
        underneath[cell] = underline
        ink[cell], paper[cell] = (bg, fg) if rev else (fg, bg)

    hist = [0] * 16
    top = []
    for cy in range(rows):
        rowbase = cy * cols
        for ry in range(h):
            for cx in range(cols):
                cell = rowbase + cx
                if underneath[cell] and ry == h - 1:
                    line = [ink[cell]] * 8
                else:
                    b = gen[codes[cell] * GLYPH_BYTES + ry]
                    line = [ink[cell] if (b >> (7 - i)) & 1 else paper[cell]
                            for i in range(8)]
                for c in line:
                    hist[c] += 1
                if cy == 0 and ry == 0:
                    top.extend(line)
    first = "".join("%x" % c for c in top)
    return {"columns": cols, "rows": rows, "char_height": h, "width": cols * 8, "height": rows * h,
            "histogram": hist, "colors_used": sum(1 for x in hist if x), "first_line": first,
            "reverse_cells": reverse_cells, "underline_cells": underline_cells,
            "blink_cells": blink_cells, "alternate_cells": alternate_cells}
