"""MSX SCREEN 2 pictures saved with BSAVE (.sc2): header, VRAM tables, pattern banks, colours, sprites."""
import struct

NAMES = ("transparent", "black", "medium_green", "light_green", "dark_blue", "light_blue", "dark_red", "cyan",
         "medium_red", "light_red", "dark_yellow", "light_yellow", "dark_green", "magenta", "gray", "white")
# TMS9918 Graphics II (SCREEN 2) VRAM layout.
PGT, NT, SAT, CT = 0x0000, 0x1800, 0x1B00, 0x2000
VRAM_SIZE = 0x4000
# The 192 visible lines are split in three thirds, each with its own pattern
# generator and colour table bank (2 KiB / 2 KiB each).
BANK_SIZE = 0x0800
LINES_PER_BANK = 8 * 8          # 8 name-table rows of 8 scanlines per third
SAT_ENTRIES = 32
SPRITE_END_Y = 208             # TMS9918 end-of-sprite-list marker (&HD0)
POINTS = {"top_left": (0, 0), "x7": (7, 0), "row_1": (0, 1), "row_64": (0, 64), "center": (128, 96),
          "bottom_right": (255, 191)}


class Sc2Error(ValueError):
    pass


def parse(data):
    # BSAVE header: 0xFE, start address, end address, execute address
    # (little-endian words). The body holds VRAM bytes start..end inclusive.
    if len(data) < 7 or data[0] != 0xFE:
        raise Sc2Error("not a BSAVE file")
    start, end, execute = struct.unpack_from("<HHH", data, 1)
    if end < start:
        raise Sc2Error("BSAVE end before start")
    body = data[7:]
    # Place the dump at the address given by the header; bytes outside the
    # dump (non-zero start, end before 0x3FFF) read as 0.
    vram = bytearray(VRAM_SIZE)
    room = VRAM_SIZE - start
    vram[start:start + min(len(body), room)] = body[:room]

    def colour(x, y):
        name = vram[NT + (y // 8) * 32 + x // 8]
        bank = y // LINES_PER_BANK
        line = y % 8
        off = bank * BANK_SIZE + name * 8 + line
        # The leftmost pixel of a pattern byte is its most significant bit.
        bit = (vram[PGT + off] >> (7 - x % 8)) & 1
        c = vram[CT + off]
        # High nibble is the foreground colour, low nibble the background;
        # nibble 0 selects the transparent colour.
        return NAMES[(c >> 4) if bit else (c & 0x0F)]

    # The sprite attribute table ends at the first entry whose Y coordinate
    # is 208; entries after it are not displayed. Without the marker all 32
    # slots count.
    sprites = 0
    for i in range(SAT_ENTRIES):
        if vram[SAT + i * 4] == SPRITE_END_Y:
            break
        sprites += 1

    return {"start": "&H%04X" % start, "end": "&H%04X" % end,
            "execute": "&H%04X" % execute,
            "vram_bytes": len(body),
            "points": {k: colour(x, y) for k, (x, y) in POINTS.items()},
            "sprites": sprites}
