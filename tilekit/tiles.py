"""Tile sets exported from console ROMs (.tiles): platform tag, palette and tile data."""
import struct

BPP = {"NES": 2, "GB": 2, "SNES": 4, "MD": 4, "GBA": 4}
TILE_SIZE = {platform: 8 * bpp for platform, bpp in BPP.items()}

# BGP values select one of the Game Boy's four fixed grey shades.
GB_SHADES = ("#ffffff", "#aaaaaa", "#555555", "#000000")


class TileError(ValueError):
    pass


def _scale_component(value, maximum):
    """Widen a hardware colour component to the full 8-bit range."""
    return round(value * 255 / maximum)


def _colour(value, platform):
    if platform == "MD":
        red = (value >> 1) & 0x07
        green = (value >> 5) & 0x07
        blue = (value >> 9) & 0x07
        maximum = 7
    else:  # SNES and GBA use 15-bit BGR555 words.
        red = value & 0x1F
        green = (value >> 5) & 0x1F
        blue = (value >> 10) & 0x1F
        maximum = 31

    return "#%02x%02x%02x" % (
        _scale_component(red, maximum),
        _scale_component(green, maximum),
        _scale_component(blue, maximum),
    )


def _palette(data, platform, entries):
    """Return the palette and the offset at which tile data starts."""
    if platform == "NES":
        return None, 12

    if platform == "GB":
        if len(data) < 13:
            raise TileError("truncated Game Boy palette")
        bgp = data[12]
        palette = [GB_SHADES[(bgp >> (2 * index)) & 3] for index in range(4)]
        return palette, 13

    palette_bytes = 2 * entries
    end = 12 + palette_bytes
    if len(data) < end:
        raise TileError("truncated palette")

    # The Mega Drive's 68000 and ROM word order are big-endian; SNES and
    # GBA palette words are little-endian.  Accept little-endian MD words as
    # well when their reserved bits show that was the export byte order.
    if platform == "MD":
        values = [struct.unpack_from(">H", data, offset)[0] for offset in range(12, end, 2)]
        reserved_bits = 0xF111
        if any(value & reserved_bits for value in values):
            little_values = [struct.unpack_from("<H", data, offset)[0] for offset in range(12, end, 2)]
            if all((value & reserved_bits) == 0 for value in little_values):
                values = little_values
    else:
        endian = "<"
        values = [struct.unpack_from(endian + "H", data, offset)[0] for offset in range(12, end, 2)]

    palette = [_colour(value, platform) for value in values]
    return palette, end


def pixel(platform, t, x, y):
    """Colour index of pixel (x, y) of one tile's bytes t."""
    platform = platform.strip()
    bit = 7 - x

    if platform == "NES":
        return (t[y] >> bit & 1) | (t[8 + y] >> bit & 1) << 1

    if platform == "GB":
        low = t[2 * y]
        high = t[2 * y + 1]
        return (low >> bit & 1) | (high >> bit & 1) << 1

    if platform == "SNES":
        low_planes = t[2 * y]
        high_planes = t[2 * y + 1]
        upper_low_planes = t[16 + 2 * y]
        upper_high_planes = t[16 + 2 * y + 1]
        return (
            (low_planes >> bit & 1)
            | (high_planes >> bit & 1) << 1
            | (upper_low_planes >> bit & 1) << 2
            | (upper_high_planes >> bit & 1) << 3
        )

    if platform == "MD":
        # Packed nibbles, with the left pixel in the high nibble.
        byte = t[4 * y + x // 2]
        shift = 4 if x % 2 == 0 else 0
        return byte >> shift & 0x0F

    if platform == "GBA":
        # Packed nibbles; x=0 is the low nibble of the first byte in a row.
        byte = t[4 * y + x // 2]
        shift = 0 if x % 2 == 0 else 4
        return byte >> shift & 0x0F

    raise TileError("unsupported platform %r" % platform)


def parse(data):
    if len(data) < 12:
        raise TileError("not a tile set")
    if data[:4] != b"TILE":
        raise TileError("not a tile set")
    try:
        platform = bytes(data[4:8]).decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise TileError("unsupported platform") from exc
    if platform not in BPP:
        raise TileError("unsupported platform %r" % platform)
    count, entries = struct.unpack_from("<HH", data, 8)
    palette, base = _palette(data, platform, entries)
    size = TILE_SIZE[platform]
    whole = min(count, (len(data) - base) // size)
    hist = [0] * (1 << BPP[platform])
    rows = None
    last = None
    for i in range(whole):
        t = data[base + i * size:base + (i + 1) * size]
        grid = ["".join("%x" % pixel(platform, t, x, y) for x in range(8)) for y in range(8)]
        for row in grid:
            for ch in row:
                hist[int(ch, 16)] += 1
        if i == 0:
            rows = grid
        last = grid[7]
    return {"platform": platform, "bpp": BPP[platform], "tiles": count, "complete": whole == count,
            "palette": palette, "histogram": hist, "first_tile": rows, "last_row": last,
            "colors_used": sum(1 for h in hist if h)}
