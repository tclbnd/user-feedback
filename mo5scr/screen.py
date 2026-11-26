"""Thomson MO5 screen snapshots (.mo5): border register, form plane and colour plane."""

HDR = 16


class Mo5Error(ValueError):
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
    if len(data) < HDR or data[:8] != b"MO5SCRN\0":
        raise Mo5Error("not an MO5 screen snapshot")
    border = data[8] & 15
    colour = data[HDR:HDR + 8192]
    form = data[HDR + 8192:HDR + 16384]
    out = {"border": "%x" % border, "complete": len(form) == 8192}
    if not out["complete"]:
        return {**out, "colors_used": None, "foreground_pixels": None, "first_row": None, "row_100": None, "last_row": None}
    rows, used, fgp = [], set(), 0
    for ln in range(200):
        line = []
        for xb in range(40):
            f = form[ln * 40 + xb]
            a = colour[ln * 40 + xb]
            fg, bg = "%x" % (a & 15), "%x" % (a >> 4)
            for s in range(8):
                bit = f >> s & 1
                fgp += bit
                line.append(fg if bit else bg)
        used.update(line)
        rows.append(line)
    return {**out, "colors_used": len(used), "foreground_pixels": fgp,
            "first_row": _runs(rows[0]), "row_100": _runs(rows[100]), "last_row": _runs(rows[-1])}
