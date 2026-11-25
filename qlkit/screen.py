"""Sinclair QL screen dumps (.qls): 256 lines of 128 bytes in mode 4 or mode 8."""


class ScreenError(ValueError):
    pass


def decode_line(mode, row):
    out = []
    flash = 0
    for i in range(0, 128, 2):
        hi, lo = row[i], row[i + 1]
        for p in range(8):
            r = hi >> (7 - p) & 1
            g = lo >> (7 - p) & 1
            out.append(g << 2 | r << 1 | (g & r))
    return out, flash


def parse(data):
    if len(data) < 6 or data[:5] != b"QLSCR":
        raise ScreenError("not a QL screen dump")
    mode = data[5]
    if mode not in (4, 8):
        raise ScreenError("unknown mode %d" % mode)
    mem = data[6:]
    lines = min(256, len(mem) // 128)
    hist = [0] * 8
    flash = 0
    first = None
    for y in range(lines):
        px, f = decode_line(mode, mem[y * 128:(y + 1) * 128])
        flash += f
        for c in px:
            hist[c] += 1
        if y == 0:
            first = "".join(str(c) for c in px)
    return {"mode": mode, "width": 512, "lines": lines, "complete": lines == 256,
            "histogram": hist, "colors_used": sum(1 for h in hist if h), "flash_bits": flash, "first_line": first}
