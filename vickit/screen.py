"""VIC-20 screen snapshots (.v20): VIC registers, screen matrix, colour RAM, character generator."""


class SnapshotError(ValueError):
    pass


def parse(data):
    if len(data) < 24 or data[:8] != b"VIC20SNP":
        raise SnapshotError("not a VIC-20 snapshot")
    r = data[8:24]
    cols = r[2]
    rows = r[3] >> 1
    h = 8
    bg = r[15] >> 4
    n = cols * rows
    need = 24 + 2 * n + 256 * h
    if len(data) < need:
        raise SnapshotError("snapshot is truncated")
    scr = data[24:24 + n]
    col = data[24 + n:24 + 2 * n]
    gen = data[24 + 2 * n:need]
    hist = [0] * 16
    lines = []
    for cy in range(rows):
        block = [[0] * (cols * 8) for _ in range(h)]
        for cx in range(cols):
            code = scr[cy * cols + cx]
            fg = col[cy * cols + cx] & 15
            for ry in range(h):
                b = gen[code * h + ry]
                for px in range(8):
                    block[ry][cx * 8 + px] = fg if b >> (7 - px) & 1 else bg
        for line in block:
            for c in line:
                hist[c] += 1
            lines.append("".join("%x" % c for c in line))
    return {"columns": cols, "rows": rows, "char_height": h, "width": cols * 8, "height": rows * h,
            "multicolor_cells": 0, "histogram": hist, "colors_used": sum(1 for x in hist if x),
            "first_line": lines[0] if lines else None, "last_line": lines[-1] if lines else None}
