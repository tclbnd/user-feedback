"""CoCo 3 GIME graphics snapshots (.gime): $FF98-$FF9F, palette $FFB0-$FFBF and physical memory."""

MEM, HDR = 0x80000, 8 + 8 + 16
HRES = [16, 32, 48, 64, 80, 96, 128, 160]
LPF = {0: 192, 1: 200, 3: 225}
CRES = {0: 2, 1: 4, 2: 16}


class GimeError(ValueError):
    pass


def _runs(line):
    out = []
    for c in line:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


def _rgb(p):
    r, g, b = p >> 4 & 3, p >> 2 & 3, p & 3
    return "#%02x%02x%02x" % (r * 85, g * 85, b * 85)


def parse(data):
    if len(data) < HDR or data[:8] != b"GIME3SN\0":
        raise GimeError("not a GIME snapshot")
    vmode, vres, border, _, v1, v2, hreg, _ = data[8:16]
    pal = data[16:32]
    mem = data[32:32 + MEM]
    if not vmode & 0x80:
        raise GimeError("text mode is not supported")
    lpf, cres = vres >> 5 & 3, vres & 3
    if lpf not in LPF or cres not in CRES:
        raise GimeError("undefined video resolution")
    lines, colours, rowbytes = 192, CRES[cres], HRES[vres >> 2 & 7]
    bpp = {2: 1, 4: 2, 16: 4}[colours]
    width = rowbytes * 8 // bpp
    start = v1 << 8 | v2
    stride = rowbytes
    hoff = 0
    out = {"width": width, "height": lines, "colors": colours, "start": start, "complete": len(mem) == MEM,
           "border": _rgb(border), "palette": [_rgb(p) for p in pal]}
    if not out["complete"]:
        return {**out, "colors_used": None, "first_row": None, "last_row": None}
    rows, seen = [], set()
    for y in range(lines):
        base = start + y * stride
        line = []
        for xb in range(rowbytes):
            off = (xb + hoff) % 256 if hreg & 0x80 else xb
            b = mem[(base + off) % MEM]
            for k in range(8 // bpp):
                c = b >> (bpp * k) & ((1 << bpp) - 1)
                line.append(_rgb(pal[c]))
        seen.update(line)
        rows.append(line)
    return {**out, "colors_used": len(seen), "first_row": _runs(rows[0]), "last_row": _runs(rows[-1])}
