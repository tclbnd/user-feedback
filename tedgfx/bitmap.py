"""Commodore Plus/4 TED bitmap snapshots (.ted): TED registers, luminance and colour matrix, bitmap."""

HDR = 8 + 32


class TedError(ValueError):
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
    if len(data) < HDR or data[:8] != b"TEDSNAP\0":
        raise TedError("not a TED snapshot")
    r = data[8:40]
    ram = data[HDR:HDR + 65536]
    if not r[0x06] & 0x20:
        raise TedError("not a bitmap mode")
    mcm = bool(r[0x07] & 0x10)
    out = {"mode": "multicolour" if mcm else "hires", "complete": len(ram) == 65536}
    if not out["complete"]:
        return {**out, "colors_used": None, "first_row": None, "row_100": None, "last_row": None}
    bmp = (r[0x12] & 0x38) << 10
    mat = (r[0x14] & 0xF8) << 8
    bg0 = r[0x15] & 0x7F
    bg1 = r[0x16] & 0x7F
    rows, used = [], set()
    for y in range(200):
        line = []
        for cx in range(40):
            k = (y >> 3) * 40 + cx
            v = ram[mat + 0x400 + k]  # video matrix: hue nibbles
            c = ram[mat + k]          # colour memory: 3-bit luminances
            b = ram[bmp + k * 8 + (y & 7)]
            one = (c & 0x70) | (v >> 4)
            zero = ((c & 0x07) << 4) | (v & 0x0F)
            if mcm:
                for s in (6, 4, 2, 0):
                    pair = b >> s & 3
                    pix = "%02x" % (bg0, one, zero, bg1)[pair]
                    line.append(pix)
                    line.append(pix)
            else:
                for s in range(7, -1, -1):
                    line.append("%02x" % (one if b >> s & 1 else zero))
        used.update(line)
        rows.append(line)
    return {**out, "colors_used": len(used),
            "first_row": _runs(rows[0]), "row_100": _runs(rows[100]), "last_row": _runs(rows[-1])}
