"""Atari 2600 scan line logs (.tia): rebuild what the TIA draws on each line."""
import struct

REC = 22


class TiaError(ValueError):
    pass


def cells(pf0, pf1, pf2):
    """The 20 playfield cells of the left half, left to right."""
    return ([pf0 >> (7 - i) & 1 for i in range(4)] + [pf1 >> (7 - i) & 1 for i in range(8)]
            + [pf2 >> (7 - i) & 1 for i in range(8)])


def draw(rec):
    bk, pf, p0, p1, ctrl, pf0, pf1, pf2 = rec[:8]
    left = cells(pf0, pf1, pf2)
    out = []
    pfpix = 0
    for x in range(160):
        on_pf = bool((left + left)[x // 4])
        pfpix += on_pf
        out.append(pf if on_pf else bk)
    return out, pfpix


def runs(px):
    out = []
    for c in px:
        h = "%02x" % c
        if out and out[-1][0] == h:
            out[-1][1] += 1
        else:
            out.append([h, 1])
    return out


def parse(data):
    if len(data) < 8 or data[:6] != b"TIALOG":
        raise TiaError("not a scan line log")
    (n,) = struct.unpack_from("<H", data, 6)
    have = min(n, (len(data) - 8) // REC)
    colors = {}
    pfpix = 0
    first = last = None
    for y in range(have):
        px, k = draw(data[8 + y * REC:8 + (y + 1) * REC])
        pfpix += k
        for c in px:
            colors["%02x" % c] = colors.get("%02x" % c, 0) + 1
        if y == 0:
            first = runs(px)
        last = runs(px)
    return {"lines": n, "complete": have == n, "playfield_pixels": pfpix,
            "colors": {k: colors[k] for k in sorted(colors)}, "first_line": first, "last_line": last}
