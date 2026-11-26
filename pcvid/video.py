"""IBM PC display memory dumps (.pcv): CGA modes 4/5/6, EGA mode 0Dh, Hercules graphics."""

CGA16 = ["#000000", "#0000aa", "#00aa00", "#00aaaa", "#aa0000", "#aa00aa", "#aa5500", "#aaaaaa",
         "#555555", "#5555ff", "#55ff55", "#55ffff", "#ff5555", "#ff55ff", "#ffff55", "#ffffff"]

# mode -> (width, height, bytes the adapter scans)
MODES = {
    4: (320, 200, 0x4000),
    5: (320, 200, 0x4000),
    6: (640, 200, 0x4000),
    0x0D: (320, 200, 4 * 8000),
    0x80: (720, 348, 0x8000),
}


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


def _gun(low, high):
    """One EGA colour gun: a low bit gives 0xAA, the high bit adds 0x55 (0x00/0x55/0xAA/0xFF)."""
    return (0xAA if low else 0x00) + (0x55 if high else 0x00)


def _ega_colour(reg):
    """Attribute-controller palette register -> monitor colour.

    Bits 0/1/2 are the blue/green/red low bits, bits 3/4/5 the matching high bits.
    """
    r = _gun(reg & 0x04, reg & 0x20)
    g = _gun(reg & 0x02, reg & 0x10)
    b = _gun(reg & 0x01, reg & 0x08)
    return "#%02x%02x%02x" % (r, g, b)


def _cga_colours(reg):
    """CGA 3D9h colour select -> the four colours of a 320x200 graphics pixel value.

    Bits 0-3 pick the background (blue/green/red/intensity), bit 4 the
    foreground intensity and bit 5 the palette: 0 is green/red/brown,
    1 is cyan/magenta/white.
    """
    base = 8 if reg & 0x10 else 0
    palette = (3, 5, 7) if reg & 0x20 else (2, 4, 6)
    return [CGA16[reg & 0x0F]] + [CGA16[base + i] for i in palette]


def _cga640_colours(reg):
    """640x200 CGA has one bit per pixel: the background is selectable, the foreground is white."""
    return [CGA16[reg & 0x0F], CGA16[15]]


def _cga_offset(y, row_bytes):
    """CGA/MDA scan the even rows from the first half of the buffer, odd rows from the second."""
    return (0x2000 if y & 1 else 0) + (y >> 1) * row_bytes


def _hercules_offset(y, row_bytes):
    """Hercules interleaves scan lines across four 8 KB banks, four lines apart."""
    return (y & 3) * 0x2000 + (y >> 2) * row_bytes


def parse(data):
    if len(data) < 23 or data[:5] != b"PCVID":
        raise VideoError("not a PC video dump")
    mode, reg = data[5], data[6]
    pals = data[7:23]
    mem = data[23:]
    if mode not in MODES:
        raise VideoError("unsupported mode %#x" % mode)
    w, h, need = MODES[mode]
    if len(mem) < need:
        return {"mode": mode, "width": w, "height": h, "complete": False, "colors": None, "colors_used": None,
                "first_line": None, "last_line": None}

    hist, lines = {}, []
    if mode in (4, 5):
        colours = _cga_colours(reg)
    elif mode == 6:
        colours = _cga640_colours(reg)
    elif mode == 0x0D:
        colours = [_ega_colour(p & 0x3F) for p in pals]
    else:  # Hercules graphics is a monochrome monitor
        colours = (CGA16[0], CGA16[15])

    for y in range(h):
        line = []
        if mode in (4, 5):
            base = _cga_offset(y, 80)
            for x in range(w):
                v = mem[base + x // 4] >> (6 - 2 * (x % 4)) & 3
                line.append(colours[v])
        elif mode == 6:
            base = _cga_offset(y, 80)
            for x in range(w):
                v = mem[base + x // 8] >> (7 - x % 8) & 1
                line.append(colours[v])
        elif mode == 0x0D:
            # Four bit planes, plane 0 first, each 8000 bytes (200 rows of 40 bytes).
            row = y * 40
            for x in range(w):
                off = row + x // 8
                shift = 7 - x % 8
                v = ((mem[off] >> shift & 1) | (mem[8000 + off] >> shift & 1) << 1 |
                     (mem[16000 + off] >> shift & 1) << 2 | (mem[24000 + off] >> shift & 1) << 3)
                line.append(colours[v])
        else:
            # Hercules: 4 banks of 0x2000 bytes; rows interleave across the banks 90 bytes apart.
            base = _hercules_offset(y, 90)
            for x in range(w):
                v = mem[base + x // 8] >> (7 - x % 8) & 1
                line.append(colours[v])
        for c in line:
            hist[c] = hist.get(c, 0) + 1
        lines.append(line)

    return {"mode": mode, "width": w, "height": h, "complete": True, "colors": dict(sorted(hist.items())),
            "colors_used": len(hist), "first_line": _runs(lines[0]), "last_line": _runs(lines[-1])}
