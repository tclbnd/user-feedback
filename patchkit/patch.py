"""Doom-engine pictures (patch lumps): header, column offsets and posts of pixels.

Post layout (classic): topdelta (1 byte), length (1 byte), 1 padding byte,
length pixel bytes, 1 padding byte; a topdelta of 0xFF ends the column.

Tall pictures (the convention used by current source ports for graphics
taller than 254 pixels): topdelta is a 16-bit little-endian value and the
column ends with a topdelta of 0xFFFF; the rest of the post is unchanged.
"""
import struct


class PatchError(ValueError):
    pass


def _classic_column(data, p):
    """Read one column of classic posts starting at p.

    Returns (posts, ok): posts is a list of (ytop, pixels) tuples and ok is
    False when the data ran out before the 0xFF terminator.
    """
    posts = []
    size = len(data)
    while True:
        if p >= size:
            return posts, False
        ytop = data[p]
        if ytop == 0xFF:
            return posts, True
        if p + 2 > size:
            return posts, False
        n = data[p + 1]
        if p + 4 + n > size:
            return posts, False
        posts.append((ytop, data[p + 3:p + 3 + n]))
        p += n + 4


def _tall_column(data, p):
    """Read one column of tall posts (16-bit topdelta) starting at p.

    Same return convention as _classic_column; the column terminator is a
    topdelta of 0xFFFF.
    """
    posts = []
    size = len(data)
    while True:
        if p >= size:
            return posts, False
        if data[p] == 0xFF and (p + 1 >= size or data[p + 1] == 0xFF):
            return posts, True
        if p + 3 > size:
            return posts, False
        ytop = data[p] | (data[p + 1] << 8)
        n = data[p + 2]
        if p + 5 + n > size:
            return posts, False
        posts.append((ytop, data[p + 4:p + 4 + n]))
        p += n + 5


def _column(data, off):
    """Read one column, falling back to the tall-post layout when the data
    cannot be walked as classic posts (as current source ports do)."""
    posts, ok = _classic_column(data, off)
    if not ok:
        tall, tall_ok = _tall_column(data, off)
        if tall_ok:
            posts, ok = tall, True
    return posts, ok


def parse(data):
    if len(data) < 8:
        raise PatchError("not a picture lump")
    w, h, left, top = struct.unpack_from("<4h", data, 0)
    if w <= 0 or h <= 0 or len(data) < 8 + 4 * w:
        raise PatchError("not a picture lump")
    offs = struct.unpack_from("<%dI" % w, data, 8)
    grid = {}
    posts = opaque = longest = 0
    complete = True
    for x, off in enumerate(offs):
        col, ok = _column(data, off)
        if not ok:
            complete = False
        for ytop, px in col:
            posts += 1
            longest = max(longest, len(px))
            for i, v in enumerate(px):
                if 0 <= ytop + i < h:
                    if (x, ytop + i) not in grid:
                        opaque += 1
                    grid[(x, ytop + i)] = v
    pts = {"top_left": (0, 0), "center": (w // 2, h // 2), "bottom_right": (w - 1, h - 1), "bottom_left": (0, h - 1)}
    return {"width": w, "height": h, "offset": [left, top], "posts": posts, "opaque_pixels": opaque,
            "longest_post": longest, "shared_columns": w - len(set(offs)), "complete": complete,
            "points": {k: grid.get(v) for k, v in pts.items()}}
