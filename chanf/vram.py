"""Fairchild Channel F port-write logs (.cflog): replayed into the 128 x 64 video RAM and coloured per row."""
import struct

PENS = {"black": "#101010", "white": "#fdfdfd", "red": "#ff3153", "green": "#02cc5d", "blue": "#4b3ff3",
        "ltgray": "#e0e0e0", "ltgreen": "#91ffa6", "ltblue": "#ced0ff"}
# Four row palettes, four 2-bit pens each, as the F3850 video hardware maps them.
# Pen 0 is black on every colour palette; palette 0 is the black/white palette.
MAP = ["black", "white", "red", "green",
       "black", "blue", "red", "green",
       "black", "ltgray", "red", "green",
       "black", "ltgreen", "blue", "red"]


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
    col = row = latch = 0
    plotted = 0
    for i in range(count):
        port, v = data[16 + 2 * i], data[17 + 2 * i]
        if port == 0:
            # A port-0 write stores the colour latched on port 1; the data
            # byte on the bus is a dummy value. The column address then
            # advances automatically, stopping at the rightmost cell.
            vram[row][col] = latch
            plotted += 1
            if col < 127:
                col += 1
        elif port == 1:
            latch = (v >> 6) & 3
        elif port == 4:
            col = v & 0x7F
        elif port == 5:
            row = v & 0x3F
        # Writes to any other port never touch video RAM.
    pals = [0] * 4
    rows = []
    # The Channel F screen is rotated 180 degrees relative to video RAM:
    # VRAM row y, cell x appears at display row (63-y), cell (127-x).
    for dy in range(64):
        sy = 63 - dy
        # The two palette bits live in VRAM cells 1 and 2 of each row; after
        # the 180-degree rotation they sit at the right edge of the picture.
        p = ((vram[sy][1] & 1) << 1) | (vram[sy][2] & 1)
        pals[p] += 1
        rows.append([PENS[MAP[p * 4 + vram[sy][127 - dx]]] for dx in range(128)])
    return {"writes": count, "plotted": plotted, "row_palettes": pals,
            "colors_used": len({c for r in rows for c in r}),
            "first_row": _runs(rows[0]), "row_32": _runs(rows[32]), "last_row": _runs(rows[-1])}
