"""SAM Coupé screen dumps (.sam): screen mode 2 or 4 with the 16-entry palette."""


class ScreenError(ValueError):
    pass


def rgb(b):
    # SAM palette register: bit 6 is the shared BRIGHT bit, bits 5-4 green,
    # bits 3-2 red, bits 1-0 blue.  BRIGHT is the low bit of each component,
    # giving a 3-bit level per component.
    bright = b >> 6 & 1
    g = (b >> 4 & 3) << 1 | bright
    r = (b >> 2 & 3) << 1 | bright
    bl = (b & 3) << 1 | bright
    return "#%02x%02x%02x" % tuple(round(c * 255 / 7) for c in (r, g, bl))


def runs(line):
    out = []
    for c in line:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


def parse(data):
    if len(data) < 23 or data[:6] != b"SAMSCR":
        raise ScreenError("not a SAM screen dump")
    mode = data[6]
    pal = [rgb(b) for b in data[7:23]]
    mem = data[23:]
    if mode == 4:
        need = 24576
    elif mode == 2:
        need = 0x2000 + 6144
    else:
        raise ScreenError("unsupported mode %d" % mode)
    if len(mem) < need:
        raise ScreenError("screen dump is truncated")
    colors = {}
    first = None
    flash = 0
    for y in range(192):
        if mode == 4:
            idx = []
            for b in mem[y * 128:(y + 1) * 128]:
                idx += [b >> 4, b & 15]
        else:
            # Mode 2: 32 bitmap bytes per line (1 bit per pixel, MSB left),
            # attributes at 0x2000, one byte per 8-pixel cell per line:
            # bit 7 FLASH, bits 6-4 INK (CLUT 8-15), bits 3-0 PAPER (CLUT 0-15).
            idx = []
            for x in range(32):
                bits = mem[y * 32 + x]
                attr = mem[0x2000 + y * 32 + x]
                if attr & 0x80:
                    flash += 1
                ink = 8 | (attr >> 4 & 7)
                paper = attr & 15
                for px in range(8):
                    idx.append(ink if bits & 0x80 >> px else paper)
        line = [pal[i] for i in idx]
        for c in line:
            colors[c] = colors.get(c, 0) + 1
        if y == 0:
            first = runs(line)
    return {"mode": mode, "palette": pal, "colors": {k: colors[k] for k in sorted(colors)},
            "flash_cells": flash, "first_line": first}
