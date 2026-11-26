"""Thomson MO5 screen snapshots (.mo5): border register, form plane and colour plane."""

HDR = 16
PLANE = 8192
COLS = 40
ROWS = 200


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
    # The gate-array register at $A7C0 keeps the border colour in its high
    # nibble; the low nibble holds the control flags (memory plane, ...).
    border = data[8] >> 4
    # README: form (pixel) plane first, then colour plane, as the 6809 sees
    # them: 40 bytes per line, line 0 first, last 192 bytes not displayed.
    form = data[HDR:HDR + PLANE]
    colour = data[HDR + PLANE:HDR + 2 * PLANE]
    out = {"border": "%x" % border,
           "complete": len(form) == PLANE and len(colour) == PLANE}
    if not out["complete"]:
        return {**out, "colors_used": None, "foreground_pixels": None, "first_row": None, "row_100": None, "last_row": None}
    rows, used, fgp = [], set(), 0
    for ln in range(ROWS):
        line = []
        for xb in range(COLS):
            f = form[ln * COLS + xb]
            a = colour[ln * COLS + xb]
            # Colour attribute: high nibble is the ink (foreground),
            # low nibble the paper (background).
            fg, bg = "%x" % (a >> 4), "%x" % (a & 15)
            # The form byte is shifted out most-significant bit first,
            # so bit 7 is the leftmost pixel of the cell.
            for s in range(7, -1, -1):
                bit = (f >> s) & 1
                fgp += bit
                line.append(fg if bit else bg)
        used.update(line)
        rows.append(line)
    return {**out, "colors_used": len(used), "foreground_pixels": fgp,
            "first_row": _runs(rows[0]), "row_100": _runs(rows[100]), "last_row": _runs(rows[-1])}
