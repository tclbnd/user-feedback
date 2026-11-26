"""Bally Astrocade screen snapshots (.asv): colour registers, colour split, vertical blank and screen RAM."""

HDR = 8 + 16
SCANLINES = 204


class AstroError(ValueError):
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
    if len(data) < HDR or data[:8] != b"ASTROVR\0":
        raise AstroError("not an Astrocade screen snapshot")
    regs = data[8:24]
    ram = data[HDR:HDR + 4096]
    if len(ram) < 4096:
        raise AstroError("screen RAM is truncated")
    hires = regs[8] & 1
    split = regs[9] & 0x3F
    bgpix = 0
    vblank = regs[10]
    per = 40
    width = per * 4
    if hires and vblank > 4096 // per:
        raise AstroError("high-resolution screen larger than the screen RAM")
    rows, used, shown = [], set(), 0
    for s in range(SCANLINES):
        line = []
        ramline = s // 2
        visible = ramline < vblank
        shown += visible
        for x in range(width):
            b = x // 4
            bank = 0 if b < split else 4
            if visible:
                v = ram[ramline * per + b] >> (2 * (x % 4)) & 3
            else:
                v = bgpix
            line.append("%02x" % regs[bank + v])
        used.update(line)
        rows.append(line)
    return {"width": width, "scanlines_with_bitmap": shown, "colors_used": len(used),
            "first_row": _runs(rows[0]), "row_100": _runs(rows[100]), "last_row": _runs(rows[-1])}
