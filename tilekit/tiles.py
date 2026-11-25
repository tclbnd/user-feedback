"""Tile sets exported from console ROMs (.tiles): platform tag, palette and tile data."""
import struct

BPP = {"NES": 2}


class TileError(ValueError):
    pass


def pixel(platform, t, x, y):
    """Colour index of pixel (x, y) of one tile's bytes t."""
    bit = 7 - x
    return (t[y] >> bit & 1) | (t[8 + y] >> bit & 1) << 1


def parse(data):
    if len(data) < 12 or data[:4] != b"TILE":
        raise TileError("not a tile set")
    platform = data[4:8].decode("ascii").strip()
    if platform not in BPP:
        raise TileError("unsupported platform %r" % platform)
    count, entries = struct.unpack_from("<HH", data, 8)
    size = 8 * BPP[platform]
    base = 12
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
            "palette": None, "histogram": hist, "first_tile": rows, "last_row": last,
            "colors_used": sum(1 for h in hist if h)}
