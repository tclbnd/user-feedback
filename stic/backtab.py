"""Intellivision BACKTAB snapshots (.ibt): the STIC background in colour stack or foreground/background mode."""
import struct

COLOURS = ["#000000", "#002dff", "#ff3d10", "#c9cfab", "#386b3f", "#00a756", "#faea50", "#fffcff",
           "#bdacc8", "#24b8ff", "#ffb41f", "#546e00", "#ff4e57", "#a496ff", "#75cc80", "#b51a58"]
HDR = 8 + 8


class SticError(ValueError):
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
    if len(data) < HDR or data[:8] != b"INTVBT\0\0":
        raise SticError("not an Intellivision BACKTAB snapshot")
    if len(data) < HDR + 480 + 2048 + 512:
        raise SticError("snapshot is truncated")
    stack_mode = data[8] & 1
    csr = [b & 15 for b in data[9:13]]
    words = struct.unpack_from("<240H", data, HDR)
    grom = data[HDR + 480:HDR + 480 + 2048]
    gram = data[HDR + 480 + 2048:HDR + 480 + 2560]
    img = [[0] * 160 for _ in range(96)]
    sp, kinds = 0, {"grom": 0, "gram": 0, "squares": 0}
    for r in range(12):
        for c in range(20):
            w = words[r * 20 + c]
            if stack_mode:
                fg = w & 7
                bg = csr[sp]
                if w & 0x2000:
                    sp = (sp + 1) & 3
                gram_sel = bool(w & 0x800)
                loc = w & 0x07F8
            else:
                fg = w & 7
                bg = (w >> 9) & 15
                gram_sel = bool(w & 0x800)
                loc = w & 0x01F8
            kinds["gram" if gram_sel else "grom"] += 1
            mem = gram if gram_sel else grom
            for y in range(8):
                b = mem[loc + y]
                for x in range(8):
                    img[r * 8 + y][c * 8 + x] = fg if b & (1 << x) else bg
    rows = [[COLOURS[v] for v in row] for row in img]
    return {"mode": "colour-stack" if stack_mode else "foreground-background", "cards": kinds,
            "colour_pixels": [sum(row.count(i) for row in img) for i in range(16)],
            "colors_used": len({c for row in rows for c in row}),
            "first_row": _runs(rows[0]), "row_48": _runs(rows[48]), "last_row": _runs(rows[-1])}
