"""ZX Spectrum screen files (.scr): standard 6912-byte screens, ULAplus palettes, Timex hi-colour and hi-res screens."""

def points(width):
    return {"top_left": (0, 0), "top_right": (width - 1, 0), "bottom_left": (0, 191), "bottom_right": (width - 1, 191),
            "center": (width // 2, 96), "probe": (width * 100 // 256, 70)}
NORMAL, BRIGHT = 0xD7, 0xFF


class ScrError(ValueError):
    pass


def bitmap_offset(x, y):
    """Display file address of the byte holding pixel (x, y).

    The bitmap is three 2048-byte thirds (y >> 6); inside a third the character
    row (y >> 3 & 7) selects a 32-byte block and the pixel line (y & 7) an
    8-byte line within it.
    """
    return ((y >> 6) << 11) + (((y >> 3) & 7) << 5) + ((y & 7) << 8) + (x >> 3)


def spectrum(index, bright):
    level = BRIGHT if bright else NORMAL
    g, r, b = index >> 2 & 1, index >> 1 & 1, index & 1          # colour number bits: green, red, blue
    return "#%02x%02x%02x" % (r * level, g * level, b * level)


def ulaplus(byte):
    g, r, b = byte >> 5 & 7, byte >> 2 & 7, byte & 3               # GGGRRRBB
    ex3 = lambda v: v << 5 | v << 2 | v >> 1
    ex2 = lambda v: v << 6 | v << 4 | v << 2 | v
    return "#%02x%02x%02x" % (ex3(r), ex3(g), ex2(b))


def parse(data):
    size = len(data)
    if size in (6912, 6976):
        kind = "standard" if size == 6912 else "ulaplus"
    elif size == 12288:
        kind = "timex_hicolor"
    elif size == 12289:
        kind = "timex_hires"
    else:
        raise ScrError("not a screen file (%d bytes)" % size)
    pal = data[6912:6976] if kind == "ulaplus" else None

    def attr(x, y):
        if kind == "timex_hicolor":
            return data[6144 + bitmap_offset(x, y)]           # one attribute per 8 x 1 cell, laid out like the bitmap
        if kind == "timex_hires":
            return 0x38                                       # black ink on white paper
        return data[6144 + (y >> 3) * 32 + (x >> 3)]

    def pixel(x, y):
        if kind == "timex_hires":                             # 512 x 192: two 6144-byte banks, alternating per byte
            bank = 0 if (x >> 3) % 2 == 0 else 6144
            column = (x >> 4) * 8 + (x & 7)
            return data[bank + bitmap_offset(column, y)] >> (7 - (x & 7)) & 1
        return data[bitmap_offset(x, y)] >> (7 - (x & 7)) & 1

    def colour(x, y):
        a = attr(x, y)
        on = pixel(x, y)
        if kind == "ulaplus":
            return ulaplus(pal[((a >> 6) & 3) * 16 + (a & 7 if on else 8 + (a >> 3 & 7))])
        return spectrum(a & 7 if on else a >> 3 & 7, a & 0x40)

    width = 512 if kind == "timex_hires" else 256
    out = {"format": kind, "width": width, "height": 192,
           "ink_pixels": sum(bin(b).count("1") for b in (data[:6144] if kind != "timex_hires" else data[:12288])),
           "points": {k: colour(x, y) for k, (x, y) in points(width).items()}}
    if kind in ("standard", "ulaplus"):
        attrs = data[6144:6912]
        out.update(flashing_cells=sum(1 for a in attrs if a & 0x80), bright_cells=sum(1 for a in attrs if a & 0x40))
    elif kind == "timex_hicolor":
        attrs = data[6144:12288]
        out.update(flashing_cells=sum(1 for a in attrs if a & 0x80), bright_cells=sum(1 for a in attrs if a & 0x40))
    else:
        out.update(flashing_cells=0, bright_cells=0, mode=data[12288])
    return out
