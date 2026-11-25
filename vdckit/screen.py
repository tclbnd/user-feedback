"""Commodore 128 VDC snapshots (.vdc): the 80-column text screen."""

NREG = 37

# VDC register 25 enables the per-cell attribute byte when bit 6 is set.
ATTR_CONTROL_REG = 25
ATTR_ENABLE = 0x40

# Attribute-byte layout.  The low nibble is the RGBI foreground colour;
# the upper bits provide the VDC's text attributes.
ATTR_COLOR = 0x0F
ATTR_ALTERNATE = 0x10
ATTR_UNDERLINE = 0x20
ATTR_REVERSE = 0x40
ATTR_BLINK = 0x80


class VdcError(ValueError):
    pass


def parse(data):
    header_len = 8 + NREG
    if len(data) < header_len or data[:8] != b"VDCSNAP\x00":
        raise VdcError("not a VDC snapshot")

    r = data[8:header_len]
    cols, rows = r[1], r[6]
    # Register 9's low five bits give scan lines per character, minus one.
    char_height = (r[9] & 0x1F) + 1
    bg = r[26] & 0x0F
    default_fg = r[26] >> 4
    attributes_on = bool(r[ATTR_CONTROL_REG] & ATTR_ENABLE)

    cells = cols * rows
    screen_start = header_len
    attr_start = screen_start + cells
    font_start = attr_start + cells
    need = font_start + 512 * 16
    if len(data) < need:
        raise VdcError("snapshot is truncated")

    screen = data[screen_start:attr_start]
    attributes = data[attr_start:font_start]
    font = data[font_start:need]

    if attributes_on:
        reverse_cells = sum(1 for a in attributes if a & ATTR_REVERSE)
        underline_cells = sum(1 for a in attributes if a & ATTR_UNDERLINE)
        blink_cells = sum(1 for a in attributes if a & ATTR_BLINK)
        alternate_cells = sum(1 for a in attributes if a & ATTR_ALTERNATE)
    else:
        reverse_cells = underline_cells = 0
        blink_cells = alternate_cells = 0

    histogram = [0] * 16
    first_line = []

    for row in range(rows):
        row_base = row * cols
        for scan_line in range(char_height):
            for col in range(cols):
                cell = row_base + col
                code = screen[cell]
                attr = attributes[cell] if attributes_on else 0

                fg = attr & ATTR_COLOR if attributes_on else default_fg
                use_second_set = bool(attr & ATTR_ALTERNATE)
                reverse = bool(attr & ATTR_REVERSE)
                underline = bool(attr & ATTR_UNDERLINE)

                glyph = code + (256 if use_second_set else 0)
                if scan_line < 16:
                    pattern = font[glyph * 16 + scan_line]
                else:
                    pattern = 0

                # The VDC draws the underline on the character cell's final
                # scan line.  Blink-on is the visible phase requested by the
                # output contract, so it does not change the pixel pattern.
                if underline and scan_line == char_height - 1:
                    pattern = 0xFF

                ink_colour, paper_colour = (bg, fg) if reverse else (fg, bg)
                for bit in range(7, -1, -1):
                    colour = ink_colour if (pattern >> bit) & 1 else paper_colour
                    histogram[colour] += 1
                    if row == 0 and scan_line == 0:
                        first_line.append("%x" % colour)

    return {
        "columns": cols,
        "rows": rows,
        "char_height": char_height,
        "width": cols * 8,
        "height": rows * char_height,
        "histogram": histogram,
        "colors_used": sum(1 for count in histogram if count),
        "first_line": "".join(first_line),
        "reverse_cells": reverse_cells,
        "underline_cells": underline_cells,
        "blink_cells": blink_cells,
        "alternate_cells": alternate_cells,
    }
