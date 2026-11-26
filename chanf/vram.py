"""Fairchild Channel F port-write logs (.cflog): replayed into the 128 x 64 video RAM and coloured per row."""
import struct

PENS = {"black": "#101010", "white": "#fdfdfd", "red": "#ff3153", "green": "#02cc5d", "blue": "#4b3ff3",
        "ltgray": "#e0e0e0", "ltgreen": "#91ffa6", "ltblue": "#ced0ff"}
MAP = ["black", "white", "red", "green",
       "ltblue", "blue", "red", "green",
       "ltgray", "blue", "red", "green",
       "ltgreen", "blue", "red", "green"]


class ChanFError(ValueError):
    pass


def _runs(line):
    out = []
    for c in line:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


def parse(data):
    if len(data) < 16 or data[:8] != b"CHFLOG\0\0":
        raise ChanFError("not a Channel F port log")
    _, count = struct.unpack_from("<BxxxI", data, 8)
    if len(data) < 16 + 2 * count:
        raise ChanFError("port log is truncated")
    vram = [[0] * 128 for _ in range(64)]
    val = col = row = 0
    plotted = 0
    for i in range(count):
        port, v = data[16 + 2 * i], data[17 + 2 * i]
        if port == 0:
            vram[row][col] = val
            plotted += 1
        elif port == 1:
            val = (v >> 6) & 3
        elif port == 4:
            col = v & 0x7F
        elif port == 5:
            row = v & 0x3F
    pals, rows = [0] * 4, []
    for y in range(64):
        p = (vram[y][125] & 1) << 1 | (vram[y][126] & 1)
        pals[p] += 1
        rows.append([PENS[MAP[p * 4 + vram[y][x]]] for x in range(128)])
    return {"writes": count, "plotted": plotted, "row_palettes": pals,
            "colors_used": len({c for r in rows for c in r}),
            "first_row": _runs(rows[0]), "row_32": _runs(rows[32]), "last_row": _runs(rows[-1])}
