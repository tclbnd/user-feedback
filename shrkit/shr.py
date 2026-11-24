"""Apple IIgs super hi-res screens: 200 lines, per-line SCBs, 16 palettes.

The 32 KiB screen dump holds 200 scan lines of 160 bytes, 200 scan-line
control bytes (SCBs) at $7D00 and 16 palettes of 16 little-endian 0RGB colour
words at $7E00.  Each line is decoded on its own, so 320-mode and 640-mode
lines may be freely mixed in one picture.  Decoded lines are expressed on the
640-column hardware grid: a 320-mode pixel covers two columns.
"""
import struct

SCREEN_SIZE = 32768
LINES = 200
BYTES_PER_LINE = 160
SCB_BASE = 0x7D00
PALETTE_BASE = 0x7E00
PALETTES = 16

# Scan-line control byte (SCB) bit assignments.
SCB_640 = 0x80      # bit 7: 1 = 640-mode line, 0 = 320-mode line
SCB_INTERRUPT = 0x40  # bit 6: scan-line interrupt enable
SCB_FILL = 0x20     # bit 5: fill mode (320-mode lines only)
SCB_PALETTE = 0x0F  # bits 0-3: palette number (0-15)

POINTS = {"top_left": (0, 0), "x1": (1, 0), "x2": (2, 0), "x3": (3, 0), "row_1": (0, 1), "center": (320, 100),
          "bottom_right": (639, 199)}


class ShrError(ValueError):
    pass


def rgb(word):
    """Format a 12-bit 0RGB colour word (4 bits per component) as #rrggbb."""
    return "#%02x%02x%02x" % ((word >> 8 & 15) * 17, (word >> 4 & 15) * 17, (word & 15) * 17)


def _decode_line(row, scb, palettes):
    """Decode one 160-byte scan line into 640 colour words (the 640 grid).

    In every byte the high nibble is the left-hand pixel and the low nibble
    the right-hand one.  320 mode treats each nibble as a 16-colour palette
    index and doubles it across two columns; fill mode stretches each even
    pixel over the following odd pixel.  640 mode splits every nibble into
    two 2-bit pixels that select palette entries 0-3.
    """
    pal = palettes[scb & SCB_PALETTE]
    mode_640 = bool(scb & SCB_640)
    fill = bool(scb & SCB_FILL)
    out = []
    for byte in row:
        high, low = byte >> 4, byte & 0x0F
        if mode_640:
            # Four 2-bit pixels, most significant pair first; entries 0-3.
            out += [pal[high >> 2], pal[high & 3], pal[low >> 2], pal[low & 3]]
        elif fill:
            # Even pixel (high nibble) stretched over the odd pixel as well.
            colour = pal[high]
            out += [colour, colour, colour, colour]
        else:
            # High nibble is the left pixel; each pixel spans two columns.
            out += [pal[high], pal[high], pal[low], pal[low]]
    return out


def parse(data):
    if len(data) != SCREEN_SIZE:
        raise ShrError("not a super hi-res screen (%d bytes)" % len(data))
    # Colour words are stored little-endian in the 0RGB format.
    palettes = [list(struct.unpack_from("<16H", data, PALETTE_BASE + 32 * p)) for p in range(PALETTES)]
    scbs = list(data[SCB_BASE:SCB_BASE + LINES])
    lines = [
        _decode_line(data[y * BYTES_PER_LINE:(y + 1) * BYTES_PER_LINE], scbs[y], palettes)
        for y in range(LINES)
    ]
    return {
        "modes": {
            "320": sum(1 for s in scbs if not s & SCB_640),
            "640": sum(1 for s in scbs if s & SCB_640),
        },
        "fill_lines": sum(1 for s in scbs if (not s & SCB_640) and (s & SCB_FILL)),
        "interrupt_lines": sum(1 for s in scbs if s & SCB_INTERRUPT),
        "palettes_used": sorted({s & SCB_PALETTE for s in scbs}),
        "points": {k: rgb(lines[y][x]) for k, (x, y) in POINTS.items()},
        "colors": len({c & 0xFFF for ln in lines for c in ln}),
    }
