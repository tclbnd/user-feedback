"""ZX Spectrum screen files (.scr): standard 6912-byte screens, ULAplus palettes, Timex hi-colour and hi-res screens."""

def points(width):
    return {"top_left": (0, 0), "top_right": (width - 1, 0), "bottom_left": (0, 191), "bottom_right": (width - 1, 191),
            "center": (width // 2, 96), "probe": (width * 100 // 256, 70)}
NORMAL, BRIGHT = 0xD7, 0xFF


class ScrError(ValueError):
    pass


def bitmap_offset(x, y):
    """Display-file offset of the byte containing pixel (x, y).

    The Spectrum/Timex bitmap is split into three 64-line sections.  Inside a
    section, the eight lines of a character row are 256 bytes apart, while
    consecutive character rows on the same scan line are 32 bytes apart.
    """
    return ((y & 0xC0) << 5) + ((y & 0x38) << 2) + ((y & 0x07) << 8) + (x >> 3)


def spectrum(index, bright):
    level = BRIGHT if bright else NORMAL
    g, r, b = index >> 2 & 1, index >> 1 & 1, index & 1          # colour number bits: green, red, blue
    return "#%02x%02x%02x" % (r * level, g * level, b * level)


def _expand3(value):
    """Repeat a three-bit component through all eight output bits."""
    return (value << 5) | (value << 2) | (value >> 1)


def _expand2(value):
    """Repeat a two-bit component through all eight output bits."""
    return value * 0x55


def ulaplus_colour(value):
    """Decode one GRB 3-3-2 ULAplus palette byte as ``#rrggbb``."""
    green = _expand3((value >> 5) & 7)
    red = _expand3((value >> 2) & 7)
    blue = _expand2(value & 3)
    return "#%02x%02x%02x" % (red, green, blue)


# Common aliases for the British/American spelling used by callers.
ulaplus_color = ulaplus_colour
ulaplus = ulaplus_colour


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

    def attr(x, y):
        if kind == "timex_hicolor":
            # One attribute byte for each 8x1 cell; the attribute bank uses
            # the same interleaved addresses as the bitmap bank.
            return data[6144 + bitmap_offset(x, y)]
        if kind == "timex_hires":
            return data[12288]
        return data[6144 + (y >> 3) * 32 + (x >> 3)]

    def pixel(x, y):
        if kind == "timex_hires":
            # The two 6 KiB banks supply alternate 8-pixel byte columns.
            byte_column = x >> 3
            bank = 6144 if byte_column & 1 else 0
            local_x = ((byte_column >> 1) << 3) | (x & 7)
            value = data[bank + bitmap_offset(local_x, y)]
        else:
            value = data[bitmap_offset(x, y)]
        return value >> (7 - (x & 7)) & 1

    def colour(x, y):
        a = attr(x, y)
        on = pixel(x, y)
        index = a & 7 if on else (a >> 3) & 7

        if kind == "timex_hires":
            # The mode byte carries ink/paper in ordinary attribute bit
            # positions; its upper bits select the mode and are not bright.
            return spectrum(index, False)

        bright = bool(a & 0x40)
        if pal is not None:
            return ulaplus_colour(pal[index + (8 if bright else 0)])
        return spectrum(index, bright)

    width = 512 if kind == "timex_hires" else 256
    bitmap_size = 12288 if kind == "timex_hires" else 6144
    out = {"format": kind, "width": width, "height": 192,
           "ink_pixels": sum(bin(b).count("1") for b in data[:bitmap_size]),
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
