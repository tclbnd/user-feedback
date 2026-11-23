"""CD+G karaoke graphics (.cdg): subcode packets, TV graphics instructions, screen memory."""
from collections import Counter
from fractions import Fraction
from functools import lru_cache

PACKET = 24
RATE = 300            # subcode packets per second: 4 per sector over 75 sectors
W, H = 300, 216       # screen memory: 50 x 18 tiles of 6 x 12 pixels
TILE_W, TILE_H = 6, 12
TILE_COLS, TILE_ROWS = W // TILE_W, H // TILE_H
BORDER_X, BORDER_Y = 6, 12     # border around the visible 288 x 192 area
MASK = 0x3F           # subcode payload lives in the low six bits; the top two are P and Q
GRAPHICS = 0x09
PRESET_REPEAT = 16    # one memory preset is sent as sixteen identical packets
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


@lru_cache(maxsize=None)
def tile_row(bits, c0, c1):
    """One six-pixel tile row as colour indices, most significant bit leftmost."""
    return bytes(c1 if bits & (0x20 >> x) else c0 for x in range(TILE_W))


def parse(data):
    if len(data) % PACKET:
        raise CdgError("file size is not a whole number of packets")
    packets = len(data) // PACKET
    screen = bytearray(W * H)
    table = ["#000000"] * 16
    border = 0
    transparent = None
    counts = Counter()
    clears = []
    graphics = tiles = 0
    first_tile = None
    scroll_h = scroll_v = 0
    preset_colour = None      # a clear is repeated PRESET_REPEAT times, so count the group once
    preset_repeats = 0
    preset_index = -1         # the repeat counter data[1] of the last preset packet seen
    for k in range(packets):
        p = data[k * PACKET:(k + 1) * PACKET]
        if (p[0] & MASK) != GRAPHICS:
            continue
        graphics += 1
        inst = p[1] & MASK
        d = [b & MASK for b in p[4:20]]
        counts[NAMES.get(inst, "type_%d" % inst)] += 1
        if inst == 1:
            c = d[0] & 0x0F
            # data[1] counts the repeats 0..15 when the ripper fills it in. A counter that drops
            # back to 0 after counting up starts a new group, so two same-coloured clears in a
            # row are still two clears; rippers that leave the counter at 0 group by colour alone.
            restarts = d[1] == 0 and preset_index > 0
            if (preset_repeats and preset_repeats < PRESET_REPEAT and c == preset_colour
                    and not restarts):
                preset_repeats += 1          # another copy of the clear already recorded
            else:
                screen[:] = bytes((c,)) * (W * H)
                clears.append(str(Fraction(k, RATE)))
                preset_colour, preset_repeats = c, 1
            preset_index = d[1]
            continue
        preset_repeats = 0
        preset_index = -1
        if inst == 2:
            border = d[0] & 0x0F
        elif inst in (6, 38):
            c0, c1, row, col = d[0] & 0x0F, d[1] & 0x0F, d[2] & 0x1F, d[3] & MASK
            if row >= TILE_ROWS or col >= TILE_COLS:   # off screen memory: draws nothing
                continue
            if first_tile is None:
                first_tile = str(Fraction(k, RATE))
            tiles += 1
            base = row * TILE_H * W + col * TILE_W
            for y in range(TILE_H):
                off = base + y * W
                bits = d[4 + y]
                if inst == 6:
                    screen[off:off + TILE_W] = tile_row(bits, c0, c1)
                else:
                    for x in range(TILE_W):
                        screen[off + x] ^= c1 if bits & (0x20 >> x) else c0
        elif inst == 28:
            transparent = d[0] & 0x0F
        elif inst == 20:                 # scroll preset: colour, then horizontal and vertical offset
            scroll_h, scroll_v = d[1] & 0x07, d[2] & 0x0F
        elif inst == 24:                 # scroll copy: horizontal and vertical offset
            scroll_h, scroll_v = d[0] & 0x07, d[1] & 0x0F
        elif inst in (30, 31):
            base = 0 if inst == 30 else 8
            for j in range(8):
                table[base + j] = colour(d[2 * j], d[2 * j + 1])
    hist = Counter(screen)
    visible = Counter()
    for y in range(BORDER_Y, H - BORDER_Y):
        off = y * W
        visible.update(screen[off + BORDER_X:off + W - BORDER_X])
    return {"duration": str(Fraction(packets, RATE)), "packets": packets,
            "graphics_packets": graphics, "instructions": dict(sorted(counts.items())),
            "first_tile": first_tile, "clears": clears, "colors": table, "border": border,
            "transparent": transparent,
            "pixels": {str(c): hist[c] for c in sorted(hist)},
            "visible_pixels": {str(c): visible[c] for c in sorted(visible)},
            "tiles_drawn": tiles, "scroll_offset": {"h": scroll_h, "v": scroll_v}}
