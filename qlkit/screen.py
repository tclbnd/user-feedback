"""Sinclair QL screen dumps (.qls): 256 lines of 128 bytes in mode 4 or mode 8."""

LINE_BYTES = 128
MAX_LINES = 256

# Pixels per line in each display mode, as read by the QL hardware.
WIDTH = {4: 512, 8: 256}


class ScreenError(ValueError):
    pass


def decode_line(mode, row):
    """Decode a 128-byte scan line to (pixels, flash bits).

    ``pixels`` are QL colour numbers from left to right (0 black, 1 blue,
    2 red, 3 magenta, 4 green, 5 cyan, 6 yellow, 7 white).
    """
    if mode == 4:
        # 512 pixels, in 64 groups of 8.  Each group is a green-plane byte
        # followed by a red-plane byte; bit 7 is the leftmost pixel.  Where
        # both planes are set the analogue output is colour 7 (white).
        out = []
        for i in range(0, LINE_BYTES, 2):
            green, red = row[i], row[i + 1]
            for p in range(7, -1, -1):
                g = (green >> p) & 1
                r = (red >> p) & 1
                out.append(g << 2 | r << 1 | (g & r))
        return out, 0

    # Mode 8: two pixels per byte, leftmost in the high nibble.  A nibble is
    # F,G,R,B from bit 3 down to bit 0, i.e. the QL colour number in bits 2-0
    # with the flash bit above it.
    out = []
    flash = 0
    for byte in row:
        for nibble in (byte >> 4, byte & 0x0F):
            out.append(nibble & 0x07)
            flash += (nibble >> 3) & 1
    return out, flash


def parse(data):
    if len(data) < 6 or data[:5] != b"QLSCR":
        raise ScreenError("not a QL screen dump")
    mode = data[5]
    if mode not in WIDTH:
        raise ScreenError("unknown mode %d" % mode)
    mem = data[6:]
    lines = min(MAX_LINES, len(mem) // LINE_BYTES)
    hist = [0] * 8
    flash = 0
    first = None
    for y in range(lines):
        px, f = decode_line(mode, mem[y * LINE_BYTES:(y + 1) * LINE_BYTES])
        flash += f
        for c in px:
            hist[c] += 1
        if y == 0:
            first = "".join(str(c) for c in px)
    return {"mode": mode, "width": WIDTH[mode], "lines": lines, "complete": lines == MAX_LINES,
            "histogram": hist, "colors_used": sum(1 for h in hist if h), "flash_bits": flash, "first_line": first}
