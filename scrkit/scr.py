"""ZX Spectrum screen files (.scr): standard 6912-byte screens, ULAplus palettes, Timex hi-colour and hi-res screens."""

def points(width):
    return {"top_left": (0, 0), "top_right": (width - 1, 0), "bottom_left": (0, 191), "bottom_right": (width - 1, 191),
            "center": (width // 2, 96), "probe": (width * 100 // 256, 70)}
NORMAL, BRIGHT = 0xD7, 0xFF


class ScrError(ValueError):
    pass


def bitmap_offset(x, y):
    """Display file address of the byte holding pixel (x, y): the screen is three thirds of 8 character rows."""
    return y * 32 + (x >> 3)


def spectrum(index, bright):
    level = NORMAL
    g, r, b = index >> 2 & 1, index >> 1 & 1, index & 1          # colour number bits: green, red, blue
    return "#%02x%02x%02x" % (r * level, g * level, b * level)


def parse(data):
    size = len(data)
    if size == 6912:
        kind = "standard"
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
        if kind == "timex_hires":                             # 512 x 192: even columns in bank 1, odd in bank 2
            bank = 0 if (x >> 3) % 2 == 0 else 6144
            return data[bank + bitmap_offset((x >> 1) & ~7 | x & 7, y)] >> (7 - (x & 7)) & 1
        return data[bitmap_offset(x, y)] >> (x & 7) & 1

    def colour(x, y):
        a = attr(x, y)
        on = pixel(x, y)
        return spectrum(a & 7 if on else a >> 4 & 7, a & 0x40)

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
