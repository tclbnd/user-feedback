"""Sharp MZ-700 screen snapshots (.mzv): character VRAM, colour VRAM and the character generator."""

HDR = 8
COLS, ROWS = 40, 25


class MzError(ValueError):
    pass


def _rgb(v):
    return "#%02x%02x%02x" % (255 if v & 1 else 0, 255 if v & 2 else 0, 255 if v & 4 else 0)


def _runs(line):
    out = []
    for c in line:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


def parse(data):
    if len(data) < HDR or data[:8] != b"MZ700VR\0":
        raise MzError("not an MZ-700 screen snapshot")
    vram = data[HDR:HDR + 1000]
    cram = data[HDR + 1000:HDR + 2000]
    cg = data[HDR + 2000:HDR + 2000 + 4096]
    out = {"width": COLS * 8, "height": ROWS * 8, "complete": len(cg) == 4096}
    if not out["complete"] or len(cram) < 1000:
        out["complete"] = False
        return {**out, "alt_cells": None, "colors_used": None, "first_row": None, "row_100": None, "last_row": None}
    rows, used, alt = [], set(), 0
    for r in range(ROWS):
        lines = [[] for _ in range(8)]
        for c in range(COLS):
            k = r * COLS + c
            a = cram[k]
            fg, bg = _rgb(a & 7), _rgb(a >> 4 & 7)
            code = vram[k]
            for y in range(8):
                g = cg[code * 8 + y]
                for x in range(8):
                    lines[y].append(fg if g >> (7 - x) & 1 else bg)
        for ln in lines:
            used.update(ln)
            rows.append(ln)
    return {**out, "alt_cells": alt, "colors_used": len(used),
            "first_row": _runs(rows[0]), "row_100": _runs(rows[100]), "last_row": _runs(rows[-1])}
