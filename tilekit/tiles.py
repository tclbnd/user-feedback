"""Tile sets exported from console ROMs (.tiles): platform tag, palette and tile data."""
import struct

# Game Boy hardware shades, indexed by the 2-bit shade number.
GB_SHADES = ("#ffffff", "#aaaaaa", "#555555", "#000000")


class TileError(ValueError):
    pass


# --- tile pixels -----------------------------------------------------------
# Each decoder returns the colour index (0..3 for 2bpp, 0..15 for 4bpp) of
# pixel (x, y) of one tile's raw bytes, as the console lays them out in VRAM.

def _nes(t, x, y):
    bit = 7 - x
    return (t[y] >> bit & 1) | (t[8 + y] >> bit & 1) << 1


def _gb(t, x, y):
    bit = 7 - x
    return (t[2 * y] >> bit & 1) | (t[2 * y + 1] >> bit & 1) << 1


def _planar4(t, x, y):
    # Mega Drive: four 8-byte planes one after another.
    bit = 7 - x
    return sum((t[p * 8 + y] >> bit & 1) << p for p in range(4))


def _paired4(t, x, y):
    # SNES and PC Engine: two row-interleaved bitplane pairs (planes 0/1 in
    # the first 16 bytes, planes 2/3 in the second).
    bit = 7 - x
    lo, hi = t[2 * y], t[2 * y + 1]
    lo2, hi2 = t[16 + 2 * y], t[17 + 2 * y]
    return (lo >> bit & 1) | (hi >> bit & 1) << 1 | (lo2 >> bit & 1) << 2 | (hi2 >> bit & 1) << 3


def _packed_high(t, x, y):
    # Two pixels per byte, high nibble on the left; Master System and Game Gear.
    byte = t[y * 4 + x // 2]
    return byte >> 4 if x % 2 == 0 else byte & 0xF


def _packed_low(t, x, y):
    # Two pixels per byte, low nibble on the left; Game Boy Advance.
    byte = t[y * 4 + x // 2]
    return byte & 0xF if x % 2 == 0 else byte >> 4


# --- palettes --------------------------------------------------------------
# Each decoder widens one entry to #rrggbb as round(c * 255 / max).

def _rgb(r, g, b, top):
    return "#%02x%02x%02x" % tuple(round(c * 255 / top) for c in (r, g, b))


def _snes_word(w):          # xBBBBBGGGGGRRRRR, 5 bits per channel
    return _rgb(w & 0x1F, w >> 5 & 0x1F, w >> 10 & 0x1F, 31)


def _md_word(w):            # ----BBB-GGG-RRR-, 3 bits per channel
    return _rgb(w >> 1 & 7, w >> 5 & 7, w >> 9 & 7, 7)


def _gg_word(w):            # ----BBBBGGGGRRRR, 4 bits per channel
    return _rgb(w & 0xF, w >> 4 & 0xF, w >> 8 & 0xF, 15)


def _pce_word(w):           # ----GGGRRRBBB, 3 bits per channel
    return _rgb(w >> 3 & 7, w >> 6 & 7, w & 7, 7)


def _sms_byte(b):           # 00BBGGRR, 2 bits per channel
    return _rgb(b & 3, b >> 2 & 3, b >> 4 & 3, 3)


def _gb_palette(bgp):
    # BGP selects one of the four fixed shades for each colour index.
    return [GB_SHADES[bgp >> (2 * i) & 3] for i in range(4)]


# --- platform table --------------------------------------------------------

PLATFORMS = {
    "NES":  {"bpp": 2, "palette": "none", "tile": _nes},
    "GB":   {"bpp": 2, "palette": "gb",   "tile": _gb},
    "SNES": {"bpp": 4, "palette": "snes", "tile": _paired4},
    "MD":   {"bpp": 4, "palette": "md",   "tile": _planar4},
    "GBA":  {"bpp": 4, "palette": "snes", "tile": _packed_low},
    "SMS":  {"bpp": 4, "palette": "sms",  "tile": _packed_high},
    "GG":   {"bpp": 4, "palette": "gg",   "tile": _packed_high},
    "PCE":  {"bpp": 4, "palette": "pce",  "tile": _paired4},
}

_WORD = {"snes": _snes_word, "md": _md_word, "gg": _gg_word, "pce": _pce_word}


def pixel(platform, t, x, y):
    """Colour index of pixel (x, y) of one tile's bytes t."""
    spec = PLATFORMS.get(platform)
    if spec is None:
        raise TileError("unsupported platform %r" % platform)
    return spec["tile"](t, x, y)


def _read_palette(kind, data, pos, entries):
    """Decode the palette at pos; return (palette, bytes consumed)."""
    if kind == "none":
        return None, 0
    avail = max(0, len(data) - pos)
    if kind == "gb":
        # The one-byte BGP register describes all four shades, independent of
        # the header's entry count.
        if not avail:
            return [], 0
        return _gb_palette(data[pos]), 1
    if kind == "sms":
        n = min(entries, avail)
        return [_sms_byte(data[pos + i]) for i in range(n)], n
    n = min(entries, avail // 2)
    decode = _WORD[kind]
    return [decode(struct.unpack_from("<H", data, pos + 2 * i)[0]) for i in range(n)], 2 * n


def parse(data):
    if len(data) < 12 or data[:4] != b"TILE":
        raise TileError("not a tile set")
    try:
        platform = data[4:8].decode("ascii").strip()
    except UnicodeDecodeError:
        raise TileError("not a tile set")
    spec = PLATFORMS.get(platform)
    if spec is None:
        raise TileError("unsupported platform %r" % platform)
    count, entries = struct.unpack_from("<HH", data, 8)
    palette, used = _read_palette(spec["palette"], data, 12, entries)
    size = 8 * spec["bpp"]
    base = 12 + used
    whole = min(count, max(0, (len(data) - base) // size))
    hist = [0] * (1 << spec["bpp"])
    rows = None
    last = None
    for i in range(whole):
        t = data[base + i * size:base + (i + 1) * size]
        grid = ["".join("%x" % spec["tile"](t, x, y) for x in range(8)) for y in range(8)]
        for row in grid:
            for ch in row:
                hist[int(ch, 16)] += 1
        if i == 0:
            rows = grid
        last = grid[7]
    return {"platform": platform, "bpp": spec["bpp"], "tiles": count, "complete": whole == count,
            "palette": palette, "histogram": hist, "first_tile": rows, "last_row": last,
            "colors_used": sum(1 for h in hist if h)}

