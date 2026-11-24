"""MSX SCREEN 2 pictures saved with BSAVE (.sc2): header, VRAM tables, pattern banks, colours, sprites."""
import struct

NAMES = ("transparent", "black", "medium_green", "light_green", "dark_blue", "light_blue", "dark_red", "cyan",
         "medium_red", "light_red", "dark_yellow", "light_yellow", "dark_green", "magenta", "gray", "white")
PGT, NT, SAT, CT = 0x0000, 0x1800, 0x1B00, 0x2000
POINTS = {"top_left": (0, 0), "x7": (7, 0), "row_1": (0, 1), "row_64": (0, 64), "center": (128, 96),
          "bottom_right": (255, 191)}


class Sc2Error(ValueError):
    pass


def parse(data):
    if len(data) < 7 or data[0] != 0xFE:
        raise Sc2Error("not a BSAVE file")
    start, end, execute = struct.unpack_from("<HHH", data, 1)
    if end < start:
        raise Sc2Error("BSAVE end before start")
    vram = bytearray(0x4000)
    body = data[7:7 + 0x4000]
    if start < 0x4000:
        chunk = body[:0x4000 - start]
        vram[start:start + len(chunk)] = chunk

    def colour(x, y):
        name = vram[NT + (y // 8) * 32 + x // 8]
        off = (y // 64) * 0x800 + name * 8 + y % 8
        bit = vram[PGT + off] >> (7 - x % 8) & 1
        c = vram[CT + off]
        return NAMES[c >> 4 if bit else c & 0x0F]
    sprites = 0
    for i in range(32):
        if vram[SAT + i * 4] == 0xD1:
            break
        sprites += 1
    return {"start": "&H%04X" % start, "end": "&H%04X" % end, "execute": "&H%04X" % execute,
            "vram_bytes": len(body), "points": {k: colour(x, y) for k, (x, y) in POINTS.items()}, "sprites": sprites}
