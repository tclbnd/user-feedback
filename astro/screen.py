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
    bgpix = regs[9] >> 6
    split_byte = split * 2
    vblank = regs[10]
    stride = 80 if hires else 40
    width = stride * 4
    if hires and vblank > 4096 // stride:
        raise AstroError("high-resolution screen larger than the screen RAM")
    rows, used, shown = [], set(), 0
    for s in range(SCANLINES):
        line = []
        ramline = s if hires else s // 2
        visible = s < vblank
        shown += visible
        for x in range(width):
            b = x // 4
            bank = 4 if b < split_byte else 0
            if visible:
                v = ram[ramline * stride + b] >> (6 - 2 * (x % 4)) & 3
            else:
                v = bgpix
            line.append("%02x" % regs[bank + v])
        used.update(line)
        rows.append(line)
    return {"width": width, "scanlines_with_bitmap": shown, "colors_used": len(used),
            "first_row": _runs(rows[0]), "row_100": _runs(rows[100]), "last_row": _runs(rows[-1])}
