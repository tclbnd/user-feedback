"""IBM PC display memory dumps (.pcv): CGA modes 4/5/6, EGA mode 0Dh, Hercules graphics."""

CGA16 = ["#000000", "#0000aa", "#00aa00", "#00aaaa", "#aa0000", "#aa00aa", "#aaaa00", "#aaaaaa",
         "#555555", "#5555ff", "#55ff55", "#55ffff", "#ff5555", "#ff55ff", "#ffff55", "#ffffff"]


class VideoError(ValueError):
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
    if len(data) < 23 or data[:5] != b"PCVID":
        raise VideoError("not a PC video dump")
    mode, reg = data[5], data[6]
    mem = data[23:]
    if mode in (4, 5):
        w, h, need = 320, 200, 0x4000
        colors = [CGA16[0], CGA16[3], CGA16[5], CGA16[7]]
    elif mode == 6:
        w, h, need = 640, 200, 0x4000
        colors = ["#000000", "#ffffff"]
    else:
        raise VideoError("unsupported mode %#x" % mode)
    if len(mem) < need:
        return {"mode": mode, "width": w, "height": h, "complete": False, "colors": None, "colors_used": None,
                "first_line": None, "last_line": None}
    hist, lines = {}, []
    for y in range(h):
        line = []
        for x in range(w):
            if mode in (4, 5):
                b = mem[y * 80 + x // 4]
                v = b >> (6 - 2 * (x % 4)) & 3
            else:
                v = mem[y * 80 + x // 8] >> (7 - x % 8) & 1
            c = colors[v]
            hist[c] = hist.get(c, 0) + 1
            line.append(c)
        lines.append(line)
    return {"mode": mode, "width": w, "height": h, "complete": True, "colors": dict(sorted(hist.items())),
            "colors_used": len(hist), "first_line": _runs(lines[0]), "last_line": _runs(lines[-1])}
