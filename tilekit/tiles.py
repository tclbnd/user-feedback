"""Tile sets exported from console ROMs (.tiles): platform tag, palette and tile data.

The container is written by the export tool and left byte-for-byte as the
console stores things:

* header: ``TILE``, a 4-byte platform tag, tile count and palette entry count
  (both 16-bit little-endian);
* palette: nothing for NES, the one-byte BGP register for GB, otherwise one
  16-bit colour word per entry;
* tiles: 8x8 pixels, packed in each console's own video-memory layout
  (2bpp planar on NES/GB, 4bpp on SNES/MD/GBA).
"""
import struct

BPP = {"NES": 2, "GB": 2, "SNES": 4, "MD": 4, "GBA": 4}

# Game Boy BGP register values map index -> shade, darkest first in hardware.
GB_SHADES = ("#ffffff", "#aaaaaa", "#555555", "#000000")


class TileError(ValueError):
    pass


def _widen(component, bits):
    """Widen a colour component to 8 bits: round(c * 255 / max)."""
    return round(component * 255 / ((1 << bits) - 1))


def pixel(platform, t, x, y):
    """Colour index of pixel (x, y) of one tile's bytes t."""
    bit = 7 - x
    if BPP[platform] == 2:
        # NES and GB: two bit planes, low plane first, MSB = leftmost pixel.
        return (t[y] >> bit & 1) | (t[8 + y] >> bit & 1) << 1
    if platform == "GBA":
        # GBA: two bytes per pixel, low byte first, low nibble = left pixel.
        off = y * 4 + (x >> 1)
        nibble = x & 1
        return t[off] >> (nibble << 2) & 0xF
    if platform == "MD":
        # Mega Drive: four interleaved planes, each row as
        # [p3, p2, p1, p0], MSB = leftmost pixel.
        off = y * 4
        return sum((t[off + 3 - p] >> bit & 1) << p for p in range(4))
    # SNES: four consecutive bit planes, MSB = leftmost pixel.
    return sum((t[p * 8 + y] >> bit & 1) << p for p in range(4))


def _palette(platform, entries, data, base):
    """Decode the palette bytes following the header; return (palette, new base)."""
    if platform == "NES":
        return None, base
    if platform == "GB":
        # One BGP register byte: two bits per index, index 0 in the low bits.
        bgp = data[base]
        return [GB_SHADES[bgp >> (i * 2) & 3] for i in range(4)], base + 1
    words = struct.unpack_from("<%dH" % entries, data, base)
    if platform == "MD":
        # 9-bit BGR word: bits 0-2 blue, 3-5 green, 6-8 red.
        colors = ["#%02x%02x%02x" % (
            _widen(w >> 6 & 7, 3), _widen(w >> 3 & 7, 3), _widen(w & 7, 3))
            for w in words]
    else:
        # SNES and GBA: 15-bit BGR555 (low five bits are blue).
        colors = ["#%02x%02x%02x" % (
            _widen(w >> 10 & 0x1F, 5), _widen(w >> 5 & 0x1F, 5),
            _widen(w & 0x1F, 5)) for w in words]
    return colors, base + entries * 2


def parse(data):
    if len(data) < 12 or data[:4] != b"TILE":
        raise TileError("not a tile set")
    platform = data[4:8].decode("ascii").strip()
    if platform not in BPP:
        raise TileError("unsupported platform %r" % platform)
    count, entries = struct.unpack_from("<HH", data, 8)
    bpp = BPP[platform]
    palette, base = _palette(platform, entries, data, 12)
    size = 8 * bpp
    whole = min(count, (len(data) - base) // size)
    hist = [0] * (1 << bpp)
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
    return {"platform": platform, "bpp": bpp, "tiles": count, "complete": whole == count,
            "palette": palette, "histogram": hist, "first_tile": rows, "last_row": last,
            "colors_used": sum(1 for h in hist if h)}
