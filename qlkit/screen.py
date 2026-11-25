"""Sinclair QL screen dumps (.qls): 256 lines of 128 bytes in mode 4 or mode 8."""


class ScreenError(ValueError):
    pass


def decode_line(mode, row):
    """Decode one 128-byte QL screen line.

    A mode 4 group is two bit planes (green, red) for eight pixels.  Both
    bits set is the QL's white entry.  A mode 8 group is four bit planes
    (green, red, blue, flash), also covering eight pixels.
    """
    out = []
    flash = 0

    if mode == 4:
        for i in range(0, 128, 2):
            green_bits = row[i]
            red_bits = row[i + 1]
            for bit in range(7, -1, -1):
                g = (green_bits >> bit) & 1
                r = (red_bits >> bit) & 1
                out.append(g << 2 | r << 1 | (g & r))
    else:
        for i in range(0, 128, 4):
            green_bits, red_bits, blue_bits, flash_bits = row[i:i + 4]
            for bit in range(7, -1, -1):
                g = (green_bits >> bit) & 1
                r = (red_bits >> bit) & 1
                b = (blue_bits >> bit) & 1
                f = (flash_bits >> bit) & 1
                flash += f
                out.append(g << 2 | r << 1 | b)

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
    width = 512 if mode == 4 else 256
    return {"mode": mode, "width": width, "lines": lines, "complete": lines == 256,
            "histogram": hist, "colors_used": sum(1 for h in hist if h), "flash_bits": flash, "first_line": first}
