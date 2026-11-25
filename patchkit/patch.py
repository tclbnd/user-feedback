"""Doom-engine pictures (patch lumps): header, column offsets and posts of pixels."""
import struct


class PatchError(ValueError):
    pass


def parse(data):
    if len(data) < 8:
        raise PatchError("not a picture lump")
    # width/height are unsigned words; leftoffset/topoffset are signed words.
    w, h, left, top = struct.unpack_from("<HHhh", data, 0)
    if w <= 0 or h <= 0 or len(data) < 8 + 4 * w:
        raise PatchError("not a picture lump")
    offs = struct.unpack_from("<%dI" % w, data, 8)
    grid = {}
    posts = opaque = longest = 0
    complete = True
    for x, off in enumerate(offs):
        # A post is: topdelta(1), length(1), unused(1), pixels(length), pad(1),
        # i.e. length + 4 bytes. A topdelta of 0xFF ends the column.
        p = off
        y = 0
        first = True
        while True:
            if p >= len(data):
                complete = False
                break
            delta = data[p]
            if delta == 0xFF:
                break
            if p + 2 >= len(data):
                complete = False
                break
            n = data[p + 1]
            if p + 3 + n > len(data):
                complete = False
                break
            px = data[p + 3:p + 3 + n]
            # Tall-picture convention (source ports): a topdelta of 0 on any
            # post after the first one means "continue right below the previous
            # post" instead of restarting at absolute row 0.
            if first or delta != 0:
                y = delta
            posts += 1
            longest = max(longest, n)
            for i, v in enumerate(px):
                if 0 <= y + i < h:
                    if (x, y + i) not in grid:
                        opaque += 1
                    grid[(x, y + i)] = v
            y += n
            first = False
            p += n + 4
    pts = {"top_left": (0, 0), "center": (w // 2, h // 2), "bottom_right": (w - 1, h - 1), "bottom_left": (0, h - 1)}
    return {"width": w, "height": h, "offset": [left, top], "posts": posts, "opaque_pixels": opaque,
            "longest_post": longest, "shared_columns": w - len(set(offs)), "complete": complete,
            "points": {k: grid.get(v) for k, v in pts.items()}}
