"""SAM Coupé screen dumps (.sam): screen mode 2 or 4 with the 16-entry palette."""


class ScreenError(ValueError):
    pass


# SAM palette (CLUT) register bit layout:
#   bits 1:0 blue, bits 3:2 green, bits 5:4 red, bit 6 bright (intensity).
# Each component is 2 bits; the bright bit selects the 3-bit DAC level table:
#   bright=0 -> levels [0, 2, 4, 7], bright=1 -> levels [0, 3, 5, 7].
_DAC_LEVELS = ((0, 2, 4, 7), (0, 3, 5, 7))
_HEADER_SIZE = 23
_MODE2_BITMAP = 6144
_MODE2_ATTR_BASE = 8192
_MODE2_ATTRS = 6144
_MODE4_SIZE = 24576


def rgb(b):
    """Decode one palette register byte into an ``#rrggbb`` colour."""
    table = _DAC_LEVELS[(b >> 6) & 1]
    r = table[(b >> 4) & 3]
    g = table[(b >> 2) & 3]
    bl = table[b & 3]
    return "#%02x%02x%02x" % tuple(round(c * 255 / 7) for c in (r, g, bl))


def _runs(line):
    runs = []
    for color in line:
        if runs and runs[-1][0] == color:
            runs[-1][1] += 1
        else:
            runs.append([color, 1])
    return runs


def _count(colors, line):
    for color in line:
        colors[color] = colors.get(color, 0) + 1


def _parse_mode4(mem, pal):
    if len(mem) < _MODE4_SIZE:
        raise ScreenError("screen dump is truncated")
    colors = {}
    first_line = None
    for y in range(192):
        line = []
        for b in mem[y * 128:(y + 1) * 128]:
            line.append(pal[b >> 4])
            line.append(pal[b & 15])
        _count(colors, line)
        if y == 0:
            first_line = _runs(line)
    return colors, 0, first_line


def _parse_mode2(mem, pal):
    need = _MODE2_ATTR_BASE + _MODE2_ATTRS
    if len(mem) < need:
        raise ScreenError("screen dump is truncated")
    bitmap = mem[:_MODE2_BITMAP]
    attrs = mem[_MODE2_ATTR_BASE:_MODE2_ATTR_BASE + _MODE2_ATTRS]
    # The 768 attribute cells (32 columns x 24 character rows) are linear.
    flash_cells = sum(1 for a in attrs[:768] if a & 0x80)
    colors = {}
    first_line = None
    for y in range(192):
        # Bitmap bytes are interlaced ZX-style: 3 thirds, each with 8
        # character rows of 8 scanlines, 32 bytes per scanline.
        third = y // 64
        char_row = (y // 8) & 7
        scanline = y & 7
        bm_base = third * 2048 + char_row * 256 + scanline * 32
        cell_row = y // 8
        line = []
        for col in range(32):
            attr = attrs[cell_row * 32 + col]
            bright = (attr >> 6) & 1
            ink = (attr & 7) | (bright << 3)
            paper = ((attr >> 3) & 7) | (bright << 3)
            ink_color = pal[ink]
            paper_color = pal[paper]
            byte = bitmap[bm_base + col]
            for bit in range(7, -1, -1):
                line.append(ink_color if (byte >> bit) & 1 else paper_color)
        _count(colors, line)
        if y == 0:
            first_line = _runs(line)
    return colors, flash_cells, first_line


def parse(data):
    if len(data) < _HEADER_SIZE or data[:6] != b"SAMSCR":
        raise ScreenError("not a SAM screen dump")
    mode = data[6]
    pal = [rgb(b) for b in data[7:23]]
    mem = data[_HEADER_SIZE:]
    if mode == 4:
        colors, flash_cells, first_line = _parse_mode4(mem, pal)
    elif mode == 2:
        colors, flash_cells, first_line = _parse_mode2(mem, pal)
    else:
        raise ScreenError("unsupported mode %d" % mode)
    return {"mode": mode, "palette": pal,
            "colors": {k: colors[k] for k in sorted(colors)},
            "flash_cells": flash_cells, "first_line": first_line}

