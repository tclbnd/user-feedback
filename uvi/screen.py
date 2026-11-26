"""Emerson Arcadia 2001 screen snapshots (.arc): the Signetics 2637 UVI character screen."""

HDR = 8
COLOURS = ["#ffffff", "#ffff00", "#00ffff", "#00ff00", "#ff00ff", "#ff0000", "#0000ff", "#000000"]


class UviError(ValueError):
    pass


def _runs(line):
    out = []
    for c in line:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


def _blocks():
    out = []
    for i in range(64):
        # The 2637 numbers the mosaic elements from right to left.
        top = (0x03 if i & 1 else 0) | (0x1C if i & 2 else 0) | (0xE0 if i & 4 else 0)
        bot = (0x03 if i & 8 else 0) | (0x1C if i & 16 else 0) | (0xE0 if i & 32 else 0)
        out.append(bytes([top] * 4 + [bot] * 4))
    return out


BLOCKS = _blocks()


def parse(data):
    if len(data) < HDR or data[:8] != b"ARCADIA\0":
        raise UviError("not an Arcadia screen snapshot")
    if len(data) < HDR + 0x300 + 0x38 * 8:
        raise UviError("snapshot is truncated")
    m = data[HDR:HDR + 0x300]
    builtin = data[HDR + 0x300:HDR + 0x300 + 0x1C0]
    glyphs = [builtin[8 * k:8 * k + 8] for k in range(0x38)]
    glyphs += [m[0x180 + 8 * k:0x188 + 8 * k] for k in range(8)]
    pal0, pal1 = m[0x1F8], m[0x1F9]
    if not pal1 & 0x80:
        raise UviError("double-height characters are not used")
    multi = bool(pal0 & 0x20)
    rows26 = bool(pal0 & 0x40)
    gfx_global = bool(pal0 & 0x80)
    img = []
    counts = {"text": 0, "blocks": 0}
    for r in range(26):
        if r >= 13 and not rows26:
            img += [[pal1 & 7] * 128 for _ in range(8)]
            continue
        base = 0x000 if r < 13 else 0x200
        codes = m[base + (r % 13) * 16:base + (r % 13) * 16 + 16]
        lines = [[0] * 128 for _ in range(8)]
        for c, ch in enumerate(codes):
            graphics = gfx_global or bool(ch & 0x40)
            pat = BLOCKS[ch & 0x3F] if graphics else glyphs[ch & 0x3F]
            counts["blocks" if graphics else "text"] += 1
            if multi:
                fg = (pal1 >> 3) & 7 if ch & 0x40 else (pal0 >> 3) & 7
                bg = pal1 & 7 if ch & 0x80 else pal0 & 7
            else:
                # Bit 6 chooses the inline block generator.  In normal
                # character mode bit 7 is the colour bit; using both high
                # bits here shifts every attribute one colour too dark.
                fg = ch >> 7
                bg = pal1 & 7
            for y in range(8):
                for x in range(8):
                    lines[y][c * 8 + x] = fg if pat[y] & (0x80 >> x) else bg
        img += lines
    rows = [[COLOURS[v] for v in line] for line in img]
    return {"rows": 26 if rows26 else 13, "multicolour": multi, "cells": counts,
            "colors_used": len({c for r in rows for c in r}),
            "first_line": _runs(rows[0]), "line_104": _runs(rows[104]), "last_line": _runs(rows[-1])}
