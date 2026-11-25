"""MSX2/2+ bitmap screen dumps (.v99): SCREEN 8, 10, 11 and 12."""

W, H = 256, 212


class VramError(ValueError):
    pass


def c8(v, top):
    return round(v * 255 / top)


def parse(data):
    if len(data) < 38 or data[:5] != b"V9958":
        raise VramError("not a screen dump")
    mode = data[5]
    if mode != 8:
        raise VramError("unsupported screen mode %d" % mode)
    regs = data[6:38]
    palette = []
    for i in range(16):
        rb, g = regs[2 * i], regs[2 * i + 1]
        palette.append("#%02x%02x%02x" % (c8(rb >> 4 & 7, 7), c8(g & 7, 7), c8(rb & 7, 7)))
    mem = data[38:]
    lines = min(H, len(mem) // W)
    colors = {}
    first = None
    pal_pixels = 0
    for yy in range(lines):
        row = mem[yy * W:(yy + 1) * W]
        line = []
        for b in row:
            line.append("#%02x%02x%02x" % (c8(b >> 2 & 7, 7), c8(b >> 5, 7), c8(b & 3, 3)))
        for c in line:
            colors[c] = colors.get(c, 0) + 1
        if yy == 0:
            first = line[:16]
    return {"mode": mode, "lines": lines, "complete": lines == H, "colors_used": len(colors),
            "top_colors": [[c, n] for c, n in sorted(colors.items(), key=lambda t: (-t[1], t[0]))[:5]],
            "palette_pixels": pal_pixels, "first_pixels": first}
