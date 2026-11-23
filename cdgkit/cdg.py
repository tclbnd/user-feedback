"""CD+G karaoke graphics (.cdg): subcode packets, TV graphics instructions, screen memory."""
from collections import Counter
from fractions import Fraction

PACKET = 24
RATE = 75             # sectors per second
W, H = 300, 216       # screen memory, 50 x 18 tiles of 6 x 12 pixels
GRAPHICS = 0x09
NAMES = {1: "memory_preset", 2: "border_preset", 6: "tile_block", 20: "scroll_preset", 24: "scroll_copy",
         28: "define_transparent", 30: "load_colors_low", 31: "load_colors_high", 38: "tile_block_xor"}


class CdgError(ValueError):
    pass


def colour(hi, lo):
    """12-bit colour table entry (two bytes) as #rrggbb."""
    r = (hi >> 2) & 0x0F
    g = ((hi & 0x03) << 2) | ((lo >> 4) & 0x03)
    b = lo & 0x0F
    return "#%02x%02x%02x" % (r * 17, g * 17, b * 17)


def parse(data):
    if len(data) % PACKET:
        raise CdgError("file size is not a whole number of packets")
    screen = [0] * (W * H)
    table = ["#000000"] * 16
    border = 0
    counts = Counter()
    clears = []
    graphics = 0
    first_tile = None
    for k in range(len(data) // PACKET):
        p = data[k * PACKET:(k + 1) * PACKET]
        if p[0] != GRAPHICS:
            continue
        graphics += 1
        inst = p[1]
        d = list(p[4:20])
        counts[NAMES.get(inst, "type_%d" % inst)] += 1
        if inst == 1:
            screen = [d[0] & 0x0F] * (W * H)
            clears.append(str(Fraction(k, RATE)))
        elif inst == 2:
            border = d[0] & 0x0F
        elif inst in (6, 38):
            c0, c1, row, col = d[0] & 0x0F, d[1] & 0x0F, d[2] & 0x1F, d[3] & 0x3F
            if first_tile is None:
                first_tile = str(Fraction(k, RATE))
            for y in range(12):
                bits = d[4 + y]
                for x in range(6):
                    c = c1 if bits & (0x20 >> x) else c0
                    screen[((row * 12 + y) * W + col * 6 + x) % (W * H)] = c
        elif inst in (30, 31):
            for j in range(8):
                table[j] = colour(d[2 * j], d[2 * j + 1])
    hist = Counter(screen)
    return {"duration": str(Fraction(len(data) // PACKET, RATE)), "packets": len(data) // PACKET,
            "graphics_packets": graphics, "instructions": dict(sorted(counts.items())),
            "first_tile": first_tile, "clears": clears, "colors": table, "border": border,
            "transparent": None, "pixels": {str(c): hist[c] for c in sorted(hist)}}
