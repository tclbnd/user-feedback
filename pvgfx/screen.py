"""Casio PV-1000 screen snapshots (.pv): tile map, pattern ROM banks, pattern RAM, border and render bits."""

HDR = 8 + 1 + 1024 + 1024


class PvError(ValueError):
    pass


def _rgb(c):
    return "#%02x%02x%02x" % (255 if c & 1 else 0, 255 if c & 2 else 0, 255 if c & 4 else 0)


def _runs(line):
    out = []
    for c in line:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


def parse(data):
    if len(data) < 8 or data[:8] != b"PV1000VR":
        raise PvError("not a PV-1000 snapshot")
    if len(data) < HDR:
        raise PvError("video RAM or pattern RAM is truncated")
    reg7 = data[8]
    vram = data[9:9 + 1024]
    pram = data[9 + 1024:HDR]
    prom = data[HDR:]
    bank, force, off, border = reg7 >> 5, reg7 >> 4 & 1, reg7 >> 3 & 1, reg7 & 7
    W, H = 256, 192
    img = [[border] * W for _ in range(H)]
    ram_cells, missing = 0, 0
    if not off:
        for y in range(24):
            for x in range(32):
                if x == 0 or x == 31:
                    continue  # the left and right tile columns belong to the border
                t = vram[y * 32 + x]
                if t >= 0xE0:
                    # tiles 0xe0-0xff come from the 1 KB pattern RAM, not the ROM
                    ram_cells += 1
                    blk = pram[32 * (t - 0xE0):32 * (t - 0xE0) + 32]
                else:
                    blk = prom[32 * (bank * 256 + t):32 * (bank * 256 + t) + 32]
                    if len(blk) < 32:
                        missing += 1
                        blk = bytes(32)
                for py in range(8):
                    # 32 bytes per pattern: 8 unused bytes, then the three 8-byte planes
                    p0, p1, p2 = blk[8 + py], blk[16 + py], blk[24 + py]
                    for px in range(8):
                        s = 7 - px
                        img[y * 8 + py][x * 8 + px] = (p2 >> s & 1) | (p1 >> s & 1) << 1 | (p0 >> s & 1) << 2
    rows = [[_rgb(c) for c in line] for line in img]
    return {"border": _rgb(border), "display": not off, "pattern_bank": bank, "ram_cells": ram_cells, "missing_patterns": missing,
            "colors_used": len({c for r in rows for c in r}), "first_row": _runs(rows[0]), "row_96": _runs(rows[96]), "last_row": _runs(rows[-1])}
