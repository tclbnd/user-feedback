"""ZX Spectrum screen files (.scr): standard 6912-byte screens, ULAplus palettes, Timex hi-colour and hi-res screens."""

def points(width):
    return {"top_left": (0, 0), "top_right": (width - 1, 0), "bottom_left": (0, 191), "bottom_right": (width - 1, 191),
            "center": (width // 2, 96), "probe": (width * 100 // 256, 70)}
NORMAL, BRIGHT = 0xD7, 0xFF

# Timex hi-res ink/paper colour pairs, selected by bits 5-3 of the mode byte.
HIRES_COLOURS = [(0, 7), (1, 6), (2, 5), (3, 4), (4, 3), (5, 2), (6, 1), (7, 0)]


class ScrError(ValueError):
    pass


def bitmap_offset(col, y):
    """Display file address of bitmap byte column col (0-31) on pixel row y: the screen is
    three thirds of 8 character rows, with the 8 pixel rows of each character row
    interleaved 256 bytes apart."""
    return ((y & 0xC0) << 5) + ((y & 0x07) << 8) + ((y & 0x38) << 2) + col


def spectrum(index, bright):
    level = BRIGHT if bright else NORMAL
    g, r, b = index >> 2 & 1, index >> 1 & 1, index & 1          # colour number bits: green, red, blue
    return "#%02x%02x%02x" % (r * level, g * level, b * level)   # black stays black


def ulaplus(byte):
    """ULAplus GRB 3-3-2 colour, each component widened to 8 bits by repeating its bits."""
    g, r, b = byte >> 5 & 7, byte >> 2 & 7, byte & 3
    r8 = (r << 5) | (r << 2) | (r >> 1)
    g8 = (g << 5) | (g << 2) | (g >> 1)
    b8 = (b << 6) | (b << 4) | (b << 2) | b
    return "#%02x%02x%02x" % (r8, g8, b8)


def parse(data):
    size = len(data)
    if size == 6912:
        kind = "standard"
    elif size == 6976:
        kind = "ulaplus"
    elif size == 12288:
        kind = "timex_hicolor"
    elif size == 12289:
        kind = "timex_hires"
    else:
        raise ScrError("not a screen file (%d bytes)" % size)
    pal = data[6912:6976] if kind == "ulaplus" else None
    if kind == "timex_hires":
        ink, paper = HIRES_COLOURS[data[12288] >> 3 & 7]

    def attr(x, y):
        if kind == "timex_hicolor":
            return data[6144 + bitmap_offset(x >> 3, y)]      # one attribute per 8 x 1 cell, laid out like the bitmap
        return data[6144 + (y >> 3) * 32 + (x >> 3)]

    def pixel(x, y):
        if kind == "timex_hires":                             # 512 x 192: even byte columns in bank 1, odd in bank 2
            bank = 6144 if (x >> 3) & 1 else 0
            return data[bank + bitmap_offset(x >> 4, y)] >> (7 - (x & 7)) & 1
        return data[bitmap_offset(x >> 3, y)] >> (7 - (x & 7)) & 1

    def colour(x, y):
        on = pixel(x, y)
        if kind == "timex_hires":                             # ink/paper from the mode byte, no bright
            return spectrum(ink if on else paper, False)
        a = attr(x, y)
        if kind == "ulaplus":                                 # bits 7-6: palette group, 5-3: paper, 2-0: ink
            return ulaplus(pal[(a >> 6) * 16 + (a & 7 if on else 8 + (a >> 3 & 7))])
        return spectrum(a & 7 if on else a >> 3 & 7, a & 0x40)

    width = 512 if kind == "timex_hires" else 256
    out = {"format": kind, "width": width, "height": 192,
           "ink_pixels": sum(bin(b).count("1") for b in (data[:12288] if kind == "timex_hires" else data[:6144])),
           "points": {k: colour(x, y) for k, (x, y) in points(width).items()}}
    if kind == "timex_hires":
        out.update(flashing_cells=0, bright_cells=0, mode=data[12288])
    else:
        attrs = data[6144:12288] if kind == "timex_hicolor" else data[6144:6912]
        out.update(flashing_cells=sum(1 for a in attrs if a & 0x80), bright_cells=sum(1 for a in attrs if a & 0x40))
    return out
