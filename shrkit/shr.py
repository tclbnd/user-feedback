"""Apple IIgs super hi-res screens: 200 lines, per-line SCBs, 16 palettes."""
import struct

POINTS = {"top_left": (0, 0), "x1": (1, 0), "x2": (2, 0), "x3": (3, 0), "row_1": (0, 1), "center": (320, 100),
          "bottom_right": (639, 199)}

SCB_640 = 0x80
SCB_INTERRUPT = 0x40
SCB_FILL = 0x20
SCB_PALETTE = 0x0F


class ShrError(ValueError):
    pass


def rgb(word):
    return "#%02x%02x%02x" % ((word >> 8 & 15) * 17, (word >> 4 & 15) * 17, (word & 15) * 17)


def parse(data):
    if len(data) != 32768:
        raise ShrError("not a super hi-res screen (%d bytes)" % len(data))
    # Colour words are little-endian 0RGB: R in bits 11-8, G in 7-4, B in 3-0.
    pals = [[struct.unpack_from("<H", data, 0x7E00 + 32 * p + 2 * i)[0] & 0xFFF for i in range(16)]
            for p in range(16)]
    scbs = data[0x7D00:0x7DC8]
    lines = []
    for y in range(200):
        scb = scbs[y]
        pal = pals[scb & SCB_PALETTE]
        row = data[y * 160:(y + 1) * 160]
        out = []
        if scb & SCB_640:
            # 640 mode: four 2-bit pixels per byte, most significant pair on the left.
            # The pixel's slot in the byte (0-3) picks one of four colour groups and
            # the 2-bit value (0-3) picks an entry within that group.
            for b in row:
                out += [pal[(b >> 6) & 3], pal[((b >> 4) & 3) | 4],
                        pal[((b >> 2) & 3) | 8], pal[(b & 3) | 12]]
        elif scb & SCB_FILL:
            # 320 fill mode: one pixel per byte, high nibble, four columns wide.
            for b in row:
                out += [pal[b >> 4]] * 4
        else:
            # 320 mode: two 4-bit pixels per byte, high nibble is the left pixel.
            for b in row:
                out += [pal[b >> 4]] * 2 + [pal[b & 15]] * 2
        lines.append(out)
    return {"modes": {"320": sum(1 for s in scbs if not s & SCB_640),
                      "640": sum(1 for s in scbs if s & SCB_640)},
            "fill_lines": sum(1 for s in scbs if not s & SCB_640 and s & SCB_FILL),
            "interrupt_lines": sum(1 for s in scbs if s & SCB_INTERRUPT),
            "palettes_used": sorted({s & SCB_PALETTE for s in scbs}),
            "points": {k: rgb(lines[y][x]) for k, (x, y) in POINTS.items()},
            "colors": len({c for ln in lines for c in ln})}
