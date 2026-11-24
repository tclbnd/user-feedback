"""Apple II hi-res graphics screens (.hgr / BSAVEd $2000-$3FFF): line addressing, 7 pixels per byte, colour rules."""

POINTS = {"top_left": (0, 0), "x7": (7, 0), "row_1": (0, 1), "row_8": (0, 8), "row_64": (0, 64), "center": (140, 96),
          "bottom_right": (279, 191)}


class HgrError(ValueError):
    pass


def line_offset(y):
    """Offset of line y in the 8 KiB page.

    Apple II hi-res lines are interleaved: the address is
    $2000 + 40*(y/64) + 128*((y/8) mod 8) + 1024*(y mod 8).
    The 192 lines occupy offsets 0..8183, with line 191 ending at 8184
    (hence the common 8184-byte short BSAVE).
    """
    return ((y & 7) << 10) + (((y >> 3) & 7) << 7) + ((y >> 6) * 40)


PAGE_SIZE = 8192
SHORT_SIZE = 8184


def parse(data):
    if len(data) not in (PAGE_SIZE, SHORT_SIZE):
        raise HgrError("not a hi-res screen (%d bytes)" % len(data))
    # Short BSAVEs omit the final eight bytes (offsets 8184..8191).
    page = data.ljust(PAGE_SIZE, b"\0")

    def lit(x, y):
        if not 0 <= x < 280:
            return False
        b = page[line_offset(y) + x // 7]
        # Pixel 0 of a byte is its low bit (bit 0); bit 7 is the group bit.
        return bool((b >> (x % 7)) & 1)

    def colour(x, y):
        if not lit(x, y):
            return "black"
        # Two lit pixels next to each other on the same line show white.
        if lit(x - 1, y) or lit(x + 1, y):
            return "white"
        b = page[line_offset(y) + x // 7]
        group1 = bool(b & 0x80)
        if group1:
            return "blue" if x % 2 == 0 else "orange"
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
