"""Commodore 64 Koala Painter pictures (.koa): multicolour bitmap, screen RAM, colour RAM, background."""
import struct

NAMES = ("black", "white", "red", "cyan", "purple", "green", "blue", "yellow", "orange", "brown", "light_red",
         "dark_grey", "grey", "light_green", "light_blue", "light_grey")
POINTS = {"top_left": (0, 0), "top_right": (159, 0), "row_7": (0, 7), "row_8": (0, 8), "center": (80, 100),
          "bottom_right": (159, 199)}


class KoalaError(ValueError):
    pass


def colour(body, x, y):
    cell = (y // 8) * 40 + x // 4
    byte = body[y * 40 + x // 4]
    bits = byte >> (2 * (x % 4)) & 3
    if bits == 0:
        return body[10000] & 0x0F                         # background
    if bits == 1:
        return body[8000 + cell] & 0x0F
    if bits == 2:
        return body[8000 + cell] >> 4
    return body[9000 + cell] >> 4


def parse(data):
    if len(data) >= 10001:
        load, body = None, data
    else:
        raise KoalaError("not a Koala picture (%d bytes)" % len(data))
    pts = {k: NAMES[colour(body, x, y)] for k, (x, y) in POINTS.items()}
    hist = [0] * 16
    for y in range(200):
        for x in range(160):
            hist[colour(body, x, y)] += 1
    return {"load_address": "$%04X" % load if load is not None else None, "background": NAMES[body[10000] & 0x0F],
            "points": pts, "colors_used": [NAMES[i] for i in range(16) if hist[i]],
            "most_common": NAMES[max(range(16), key=lambda i: (hist[i], -i))]}
