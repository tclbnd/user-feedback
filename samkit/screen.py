"""SAM Coupé screen dumps (.sam): screen mode 2 or 4 with the 16-entry palette."""


class ScreenError(ValueError):
    pass


def rgb(b):
    g = b >> 4 & 3
    r = b >> 2 & 3
    bl = b & 3
    return "#%02x%02x%02x" % tuple(round(c * 255 / 3) for c in (r, g, bl))


def parse(data):
    if len(data) < 23 or data[:6] != b"SAMSCR":
        raise ScreenError("not a SAM screen dump")
    mode = data[6]
    pal = [rgb(b) for b in data[7:23]]
    mem = data[23:]
    if mode == 4:
        need = 24576
    else:
        raise ScreenError("unsupported mode %d" % mode)
    if len(mem) < need:
        raise ScreenError("screen dump is truncated")
    colors = {}
    first = None
    flash = 0
    for y in range(192):
        idx = []
        for b in mem[y * 128:(y + 1) * 128]:
            idx += [b >> 4, b & 15]
        line = [pal[i] for i in idx]
        for c in line:
            colors[c] = colors.get(c, 0) + 1
        if y == 0:
            first = []
            for c in line:
                if first and first[-1][0] == c:
                    first[-1][1] += 1
                else:
                    first.append([c, 1])
    return {"mode": mode, "palette": pal, "colors": {k: colors[k] for k in sorted(colors)},
            "flash_cells": flash, "first_line": first}
