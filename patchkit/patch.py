"""Doom-engine pictures (patch lumps): header, column offsets and posts of pixels."""
import struct


class PatchError(ValueError):
    pass


def parse(data):
    if len(data) < 8:
        raise PatchError("not a picture lump")
    w, h, left, top = struct.unpack_from("<4H", data, 0)
    if w <= 0 or h <= 0 or len(data) < 8 + 4 * w:
        raise PatchError("not a picture lump")
    offs = struct.unpack_from("<%dI" % w, data, 8)
    grid = {}
    posts = opaque = longest = 0
    complete = True
    for x, off in enumerate(offs):
        p = off
        while True:
            if p >= len(data):
                complete = False
                break
            delta = data[p]
            if delta == 0xFF:
                break
            ytop = delta
            if p + 3 > len(data):
                complete = False
                break
            n = data[p + 1]
            px = data[p + 2:p + 2 + n]
            if len(px) < n:
                complete = False
                break
            posts += 1
            longest = max(longest, n)
            for i, v in enumerate(px):
                if 0 <= ytop + i < h:
                    if (x, ytop + i) not in grid:
                        opaque += 1
                    grid[(x, ytop + i)] = v
            p += n + 2
    pts = {"top_left": (0, 0), "center": (w // 2, h // 2), "bottom_right": (w - 1, h - 1), "bottom_left": (0, h - 1)}
    return {"width": w, "height": h, "offset": [left, top], "posts": posts, "opaque_pixels": opaque,
            "longest_post": longest, "shared_columns": w - len(set(offs)), "complete": complete,
            "points": {k: grid.get(v) for k, v in pts.items()}}
