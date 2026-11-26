"""Amiga OCS screen dumps (.ascr): BPLCON0/BPLCON2, colour registers, bitplanes, display modes."""
import struct


class ScreenError(ValueError):
    pass


def _hex(c):
    return "#%02x%02x%02x" % ((c >> 8 & 15) * 17, (c >> 4 & 15) * 17, (c & 15) * 17)


def _runs(line):
    out = []
    for c in line:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


def parse(data):
    if len(data) < 14 + 64 or data[:6] != b"AMISCR":
        raise ScreenError("not an Amiga screen dump")
    c0, c2, w, h = struct.unpack_from(">HHHH", data, 6)
    regs = list(struct.unpack_from(">32H", data, 14))
    planes = c0 >> 12 & 7
    if planes > 6:
        raise ScreenError("bad plane count")
    mode = "normal"
    bpr = (w + 15) // 16 * 2
    psize = bpr * h
    body = data[78:]
    if len(body) < psize * planes:
        return {"mode": mode, "planes": planes, "width": w, "height": h, "complete": False,
                "colors_used": None, "first_line": None, "last_line": None}
    seen, lines = set(), []
    for y in range(h):
        line = []
        for x in range(w):
            v = 0
            for p in range(planes):
                v = v << 1 | (body[p * psize + y * bpr + x // 8] >> (7 - x % 8) & 1)
            hx = _hex(regs[v & 31])
            seen.add(hx)
            line.append(hx)
        lines.append(line)
    return {"mode": mode, "planes": planes, "width": w, "height": h, "complete": True,
            "colors_used": len(seen), "first_line": _runs(lines[0]), "last_line": _runs(lines[-1])}
