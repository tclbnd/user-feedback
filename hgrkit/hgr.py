"""Apple II hi-res graphics screens (.hgr / BSAVEd $2000-$3FFF): line addressing, 7 pixels per byte, colour rules."""

POINTS = {"top_left": (0, 0), "x7": (7, 0), "row_1": (0, 1), "row_8": (0, 8), "row_64": (0, 64), "center": (140, 96),
          "bottom_right": (279, 191)}


class HgrError(ValueError):
    pass


def line_offset(y):
    """Offset of line y in the 8 KiB page: three interleaved groups of 64, 8 and 1 lines."""
    return (y & 7) * 0x400 + ((y >> 3) & 7) * 0x80 + (y >> 6) * 0x28


def parse(data):
    if len(data) not in (8184, 8192):
        raise HgrError("not a hi-res screen (%d bytes)" % len(data))
    page = data.ljust(8192, b"\0")

    def lit(x, y):
        if not 0 <= x < 280:
            return False
        b = page[line_offset(y) + x // 7]
        return bool(b >> (x % 7) & 1)

    def colour(x, y):
        if not lit(x, y):
            return "black"
        if lit(x - 1, y) or lit(x + 1, y):
            return "white"
        group1 = bool(page[line_offset(y) + x // 7] & 0x80)
        if x % 2 == 0:
            return "blue" if group1 else "violet"
        return "orange" if group1 else "green"

    counts = {}
    lit_total = 0
    for y in range(192):
        for x in range(280):
            if lit(x, y):
                lit_total += 1
                c = colour(x, y)
                counts[c] = counts.get(c, 0) + 1
    shown = {line_offset(y) + i for y in range(192) for i in range(40)}
    return {"bytes": len(data), "points": {k: colour(x, y) for k, (x, y) in POINTS.items()},
            "lit_pixels": lit_total, "colors": dict(sorted(counts.items())),
            "group1_bytes": sum(1 for y in range(192) for i in range(40) if page[line_offset(y) + i] & 0x80),
            "hole_bytes": sum(1 for off in range(8192) if off not in shown and page[off])}
