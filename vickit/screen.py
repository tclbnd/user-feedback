"""VIC-20 screen snapshots (.v20): VIC registers, screen matrix, colour RAM, character generator."""


class SnapshotError(ValueError):
    pass


def parse(data):
    if len(data) < 24 or data[:8] != b"VIC20SNP":
        raise SnapshotError("not a VIC-20 snapshot")

    r = data[8:24]
    cols = r[2]
    rows = r[3] >> 1
    char_height = 16 if r[3] & 0x01 else 8

    background = r[15] >> 4
    border = r[15] & 0x0F
    auxiliary = r[14] >> 4

    cells = cols * rows
    generator_size = 256 * char_height
    need = 24 + 2 * cells + generator_size
    if len(data) < need:
        raise SnapshotError("snapshot is truncated")

    screen = data[24:24 + cells]
    colour = data[24 + cells:24 + 2 * cells]
    generator = data[24 + 2 * cells:need]

    width = cols * 8
    histogram = [0] * 16
    lines = []
    multicolor_cells = 0

    for row in range(rows):
        block = [[0] * width for _ in range(char_height)]

        for col in range(cols):
            index = row * cols + col
            code = screen[index]
            nibble = colour[index] & 0x0F

            # Bit 3 of the colour RAM nibble selects a multicolour character.
            multicolour = bool(nibble & 0x08)
            if multicolour:
                multicolor_cells += 1
                foreground = nibble & 0x07
                palette = (background, border, auxiliary, foreground)
            else:
                foreground = nibble
                palette = None

            # Screen-code bit 7 is the reverse-video attribute.  In the VIC
            # character address space it selects the inverse form of the
            # corresponding seven-bit character.
            reverse = bool(code & 0x80)
            glyph_code = code & 0x7F if reverse else code
            glyph_start = glyph_code * char_height

            # Snapshot writers occasionally place an attributed glyph at its
            # full 8-bit code rather than in the seven-bit character slot.
            # Use that slot when the attributed slot is blank.
            if reverse and not any(generator[glyph_start:glyph_start + char_height]):
                full_start = code * char_height
                if any(generator[full_start:full_start + char_height]):
                    glyph_start = full_start

            x0 = col * 8
            for glyph_row in range(char_height):
                bits = generator[glyph_start + glyph_row]
                if reverse:
                    bits ^= 0xFF

                if multicolour:
                    output_line = block[glyph_row]
                    for fat_pixel in range(4):
                        pair = (bits >> (6 - 2 * fat_pixel)) & 0x03
                        colour_index = palette[pair]
                        x = x0 + 2 * fat_pixel
                        output_line[x] = colour_index
                        output_line[x + 1] = colour_index
                        histogram[colour_index] += 2
                else:
                    output_line = block[glyph_row]
                    for pixel in range(8):
                        bit = (bits >> (7 - pixel)) & 1
                        colour_index = foreground if bit else background
                        x = x0 + pixel
                        output_line[x] = colour_index
                        histogram[colour_index] += 1

        for line in block:
            lines.append("".join("%x" % colour_index for colour_index in line))

    return {
        "columns": cols,
        "rows": rows,
        "char_height": char_height,
        "width": width,
        "height": rows * char_height,
        "multicolor_cells": multicolor_cells,
        "histogram": histogram,
        "colors_used": sum(1 for count in histogram if count),
        "first_line": lines[0] if lines else None,
        "last_line": lines[-1] if lines else None,
    }
