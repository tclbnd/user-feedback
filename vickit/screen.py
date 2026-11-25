"""VIC-20 screen snapshots (.v20): VIC registers, screen matrix, colour RAM, character generator."""


class SnapshotError(ValueError):
    pass


def parse(data):
    if len(data) < 24 or data[:8] != b"VIC20SNP":
        raise SnapshotError("not a VIC-20 snapshot")
    r = data[8:24]
    cols = r[2]
    # $9003: b7 is reverse video, b6..b1 are rows, b0 selects 8/16 rows
    # of glyph data per character.
    rows = (r[3] >> 1) & 0x3F
    h = 16 if r[3] & 0x01 else 8
    reverse = bool(r[3] & 0x80)
    bg = r[15] >> 4
    border = r[15] & 0x0F
    aux = r[14] >> 4
    n = cols * rows
    need = 24 + 2 * n + 256 * h
    if len(data) < need:
        raise SnapshotError("snapshot is truncated")
    scr = data[24:24 + n]
    col = data[24 + n:24 + 2 * n]
    gen = data[24 + 2 * n:need]
    hist = [0] * 16
    multicolor_cells = 0
    first_line = None
    last_line = None

    for cy in range(rows):
        pixel_rows = [[] for _ in range(h)]
        for cx in range(cols):
            idx = cy * cols + cx
            code = scr[idx]
            color_nibble = col[idx] & 0x0F
            multi = bool(color_nibble & 0x08)
            if multi:
                multicolor_cells += 1
                fg = color_nibble & 0x07
                # VIC multicolour bit pairs select background, border,
                # auxiliary and character colour respectively.
                pair_colors = (bg, border, aux, fg)
            else:
                fg = color_nibble

            for ry in range(h):
                bits = gen[code * h + ry]
                if reverse:
                    bits ^= 0xFF

                pixels = []
                if multi:
                    for shift in (6, 4, 2, 0):
                        color = pair_colors[(bits >> shift) & 0x03]
                        # A multicolour pixel is two display pixels wide.
                        pixels.extend((color, color))
                        hist[color] += 2
                else:
                    for shift in range(7, -1, -1):
                        color = fg if (bits >> shift) & 1 else bg
                        pixels.append(color)
                        hist[color] += 1

                pixel_rows[ry].extend(pixels)

        for pixels in pixel_rows:
            line = "".join("%x" % color for color in pixels)
            if first_line is None:
                first_line = line
            last_line = line

    return {"columns": cols, "rows": rows, "char_height": h, "width": cols * 8, "height": rows * h,
            "multicolor_cells": multicolor_cells, "histogram": hist,
            "colors_used": sum(1 for x in hist if x),
            "first_line": first_line, "last_line": last_line}
