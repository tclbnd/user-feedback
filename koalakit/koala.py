"""Commodore 64 Koala Painter pictures (.koa): multicolour bitmap, screen RAM, colour RAM, background."""
import struct

NAMES = ("black", "white", "red", "cyan", "purple", "green", "blue", "yellow", "orange", "brown", "light_red",
         "dark_grey", "grey", "light_green", "light_blue", "light_grey")
POINTS = {"top_left": (0, 0), "top_right": (159, 0), "row_7": (0, 7), "row_8": (0, 8), "center": (80, 100),
          "bottom_right": (159, 199)}


class KoalaError(ValueError):
    pass


BITMAP = 0           # $2000-$3F3F, 8000 bytes
SCREEN = 8000        # $3F40-$4327, 1000 bytes
COLOUR = 9000        # $4328-$470F, 1000 bytes
BACKGROUND = 10000   # $4710, 1 byte
BODY_SIZE = 10001    # bitmap + screen RAM + colour RAM + background byte


def bitmap_offset(x, y):
    """Offset of the byte holding pixel (x, y) in the 8000-byte multicolour bitmap.

    A C64 bitmap is stored character cell by character cell: the 40 x 25 cells
    each own 8 consecutive bytes, one per scanline of the cell, and the leftmost
    pixel of a byte is bits 7-6.  It is not a plain 200-scanline raster.
    """
    return ((y // 8) * 40 + x // 4) * 8 + (y % 8)


def nibble(byte, high=False):
    return (byte >> 4) if high else (byte & 0x0F)


def colour(body, x, y):
    """Colour number of pixel (x, y) on the 160 x 200 multicolour grid."""
    cell = (y // 8) * 40 + x // 4
    byte = body[BITMAP + bitmap_offset(x, y)]
    # In multicolour mode the leftmost pixel of a byte uses bits 7-6.
    bits = (byte >> (6 - 2 * (x % 4))) & 3
    if bits == 0:
        return nibble(body[BACKGROUND])                    # background colour
    if bits == 1:
        return nibble(body[SCREEN + cell], high=True)       # screen RAM, upper nibble
    if bits == 2:
        return nibble(body[SCREEN + cell])                  # screen RAM, lower nibble
    return nibble(body[COLOUR + cell])                      # colour RAM


def parse(data):
    # 10001-byte files are a bare picture; 10003..10006-byte files start with a
    # two-byte little-endian load address, optionally followed by pad bytes.
    if len(data) == BODY_SIZE:
        load, body = None, data
    elif 10003 <= len(data) <= 10006:
        load, body = struct.unpack("<H", data[:2])[0], data[2:2 + BODY_SIZE]
    else:
        raise KoalaError("not a Koala picture (%d bytes)" % len(data))
    pts = {k: NAMES[colour(body, x, y)] for k, (x, y) in POINTS.items()}
    hist = [0] * 16
    for y in range(200):
        for x in range(160):
            hist[colour(body, x, y)] += 1
    return {"load_address": "$%04X" % load if load is not None else None,
            "background": NAMES[nibble(body[BACKGROUND])],
            "points": pts, "colors_used": [NAMES[i] for i in range(16) if hist[i]],
            "most_common": NAMES[max(range(16), key=lambda i: (hist[i], -i))]}
