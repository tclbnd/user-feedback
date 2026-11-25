"""Commodore 128 VDC snapshots (.vdc): the 80-column text screen."""

NREG = 37


class VdcError(ValueError):
    pass


def parse(data):
    if len(data) < 8 + NREG or data[:8] != b"VDCSNAP\x00":
        raise VdcError("not a VDC snapshot")
    r = data[8:8 + NREG]
    cols, rows = r[1], r[6]
    h = 8
    bg = r[26] & 15
    fg = r[26] >> 4
    n = cols * rows
    base = 8 + NREG
    need = base + 2 * n + 512 * 16
    if len(data) < need:
        raise VdcError("snapshot is truncated")
    scr = data[base:base + n]
    gen = data[base + 2 * n:need]
    hist = [0] * 16
    first = None
    for cy in range(rows):
        for ry in range(h):
            line = []
            for cx in range(cols):
                b = gen[scr[cy * cols + cx] * 16 + ry]
                line += [fg if b >> (7 - i) & 1 else bg for i in range(8)]
            for c in line:
                hist[c] += 1
            if first is None:
                first = "".join("%x" % c for c in line)
    return {"columns": cols, "rows": rows, "char_height": h, "width": cols * 8, "height": rows * h,
            "histogram": hist, "colors_used": sum(1 for x in hist if x), "first_line": first,
            "reverse_cells": 0, "underline_cells": 0, "blink_cells": 0, "alternate_cells": 0}
