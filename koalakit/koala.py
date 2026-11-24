"""Commodore 64 Koala Painter pictures (.koa): multicolour bitmap, screen RAM, colour RAM, background.

The picture body is 8000 bytes of VIC-II bitmap, 1000 bytes of screen RAM,
1000 bytes of colour RAM and one background byte.  Paint and conversion
tools may save the bare 10001-byte body or prepend the two-byte load
address, sometimes followed by a little padding.

Multicolour bitmap mode stores one bit pair per (double-wide) pixel:

    00  background colour
    01  high nibble of the screen RAM byte
    10  low nibble of the screen RAM byte
    11  low nibble of the colour RAM byte
"""
import struct

NAMES = ("black", "white", "red", "cyan", "purple", "green", "blue", "yellow", "orange", "brown", "light_red",
         "dark_grey", "grey", "light_green", "light_blue", "light_grey")
POINTS = {"top_left": (0, 0), "top_right": (159, 0), "row_7": (0, 7), "row_8": (0, 8), "center": (80, 100),
          "bottom_right": (159, 199)}

BITMAP = 0
SCREEN = 8000
COLOUR_RAM = 9000
BACKGROUND = 10000
BODY_SIZE = 10001
LOAD_ADDRESS_SIZE = 2
MAX_PADDING = 3


class KoalaError(ValueError):
    pass


def colour(body, x, y):
    """Colour number (0-15) of the multicolour pixel at (x, y)."""
    # Each 8-row character row is stored as 40 cells of 8 bytes, and inside
    # a byte the leftmost pixel uses the two most significant bits.
    bits = body[BITMAP + (y // 8) * 320 + (x // 4) * 8 + y % 8] >> (6 - 2 * (x % 4)) & 3
    if bits == 0:
        return body[BACKGROUND] & 0x0F
    cell = (y // 8) * 40 + x // 4
    if bits == 1:
        return body[SCREEN + cell] >> 4
    if bits == 2:
        return body[SCREEN + cell] & 0x0F
    return body[COLOUR_RAM + cell] & 0x0F


def parse(data):
    size = len(data)
    if size == BODY_SIZE:
        load, body = None, data
    elif BODY_SIZE + LOAD_ADDRESS_SIZE <= size <= BODY_SIZE + LOAD_ADDRESS_SIZE + MAX_PADDING:
        load = struct.unpack_from("<H", data)[0]
        body = data[LOAD_ADDRESS_SIZE:LOAD_ADDRESS_SIZE + BODY_SIZE]
    else:
        raise KoalaError("not a Koala picture (%d bytes)" % size)

    pts = {k: NAMES[colour(body, x, y)] for k, (x, y) in POINTS.items()}
    hist = [0] * 16
    for y in range(200):
        for x in range(160):
            hist[colour(body, x, y)] += 1
    return {"load_address": "$%04X" % load if load is not None else None, "background": NAMES[body[BACKGROUND] & 0x0F],
            "points": pts, "colors_used": [NAMES[i] for i in range(16) if hist[i]],
            "most_common": NAMES[max(range(16), key=lambda i: (hist[i], -i))]}
