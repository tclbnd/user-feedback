"""Apple II hi-res graphics screens (.hgr / BSAVEd $2000-$3FFF): line addressing, 7 pixels per byte, colour rules."""

POINTS = {"top_left": (0, 0), "x7": (7, 0), "row_1": (0, 1), "row_8": (0, 8), "row_64": (0, 64), "center": (140, 96),
          "bottom_right": (279, 191)}


class HgrError(ValueError):
    pass


def line_offset(y):
    """Offset of line y in the 8 KiB page: three interleaved groups of 64, 8 and 1 lines."""
    return y * 40


def parse(data):
    if len(data) != 8192:
        raise HgrError("not a hi-res screen (%d bytes)" % len(data))
    page = data.ljust(8192, b"\0")

    def lit(x, y):
        if not 0 <= x < 280:
            return False
        b = page[line_offset(y) + x // 7]
        return bool(b >> (6 - x % 7) & 1)

    def colour(x, y):
        if not lit(x, y):
            return "black"
        return "violet" if x % 2 == 0 else "green"

    counts = {}
    lit_total = 0
    for y in range(192):
        for x in range(280):
            if lit(x, y):
                lit_total += 1
                c = colour(x, y)
                counts[c] = counts.get(c, 0) + 1
    return {"bytes": len(data), "points": {k: colour(x, y) for k, (x, y) in POINTS.items()},
            "lit_pixels": lit_total, "colors": dict(sorted(counts.items())),
            "group1_bytes": sum(1 for y in range(192) for i in range(40) if page[line_offset(y) + i] & 0x80)}
