"""C64 sprite snapshots (.vsp): VIC-II registers $D000-$D02E, sprite pointers, 16 KB VIC bank."""

W, H = 320, 200

# The sprite X/Y registers position sprites relative to the whole screen,
# including the border. The 320x200 text window starts 24 pixels from the
# left border edge and 50 scanlines below the top border edge (PAL timing).
X_OFF, Y_OFF = 24, 50

BASE_W, BASE_H = 24, 21  # unexpanded sprite size

# VIC-II registers
D010_XMSB = 0x10   # bit n = 9th (MSB) X coordinate bit of sprite n
D015_ENABLE = 0x15  # bit n = sprite n enabled
D017_YEXP = 0x17   # bit n = vertical expansion of sprite n
D01C_MULTI = 0x1C  # bit n = multicolour mode of sprite n
D01D_XEXP = 0x1D   # bit n = horizontal expansion of sprite n
D021_BG = 0x21
D025_MC0 = 0x25   # shared multicolour 0 (bit pair %01)
D026_MC1 = 0x26   # shared multicolour 1 (bit pair %11)
D027_SPRCOL0 = 0x27  # sprite n individual colour (multicolour pair %10)

TRANSPARENT = -1


class SpriteError(ValueError):
    pass


def _decode_rows(blk, multicolor, own_color, mc0, mc1):
    """Decode the 63-byte sprite definition into 21 rows of 24 colour cells.

    TRANSPARENT marks transparent cells. A hi-res sprite has one cell per
    pixel; a multicolour sprite has one cell per 2-bit pair (12 pairs, each
    spanning two physical pixels)."""
    rows = []
    for row in range(BASE_H):
        bits = blk[3 * row] << 16 | blk[3 * row + 1] << 8 | blk[3 * row + 2]
        cells = []
        if multicolor:
            for i in range(0, BASE_W, 2):
                pair = (bits >> (22 - i)) & 3
                if pair == 0:
                    color = TRANSPARENT
                elif pair == 1:
                    color = mc0
                elif pair == 2:
                    color = own_color
                else:
                    color = mc1
                cells.append(color)
        else:
            for i in range(BASE_W):
                cells.append(own_color if (bits >> (23 - i)) & 1 else TRANSPARENT)
        rows.append(cells)
    return rows


def parse(data):
    if len(data) < 6 + 47 + 8 or data[:6] != b"C64SPR":
        raise SpriteError("not a sprite snapshot")
    r = data[6:53]
    ptrs = data[53:61]
    mem = data[61:61 + 16384]
    complete = len(mem) == 16384
    bg = r[D021_BG] & 15
    mc0, mc1 = r[D025_MC0] & 15, r[D026_MC1] & 15
    canvas = [[bg] * W for _ in range(H)]
    sprites = []
    for n in range(8):
        if not (r[D015_ENABLE] >> n) & 1:
            continue
        # 9-bit X coordinate: low byte in $D000+2n, MSB in $D010.
        x = r[2 * n] + 256 * ((r[D010_XMSB] >> n) & 1) - X_OFF
        y = r[2 * n + 1] - Y_OFF
        multicolor = bool((r[D01C_MULTI] >> n) & 1)
        xexp = bool((r[D01D_XEXP] >> n) & 1)
        yexp = bool((r[D017_YEXP] >> n) & 1)
        width = BASE_W * (2 if xexp else 1)
        height = BASE_H * (2 if yexp else 1)

        base = ptrs[n] * 64
        blk = mem[base:base + 63]
        if len(blk) < 63:
            sprites.append({"n": n, "x": x, "y": y, "width": width,
                            "height": height, "pixels": None, "rows": None})
            continue

        rows = _decode_rows(blk, multicolor, r[D027_SPRCOL0 + n] & 15, mc0, mc1)
        # A decoded cell covers cell_w physical pixels (1 hi-res, 2 in
        # multicolour). Each expansion axis doubles the covered pixels.
        cell_w = 2 if multicolor else 1
        step_x = cell_w * (2 if xexp else 1)
        step_y = 2 if yexp else 1
        pixels = sum(c != TRANSPARENT for row in rows for c in row) * step_x * step_y
        sprites.append({"n": n, "x": x, "y": y, "width": width, "height": height,
                        "pixels": pixels, "rows": rows,
                        "step_x": step_x, "step_y": step_y})

    # Sprite 0 has the highest display priority and sprite 7 the lowest, so
    # lower-numbered sprites cover higher-numbered ones. Draw 7 ... 0.
    for s in reversed(sprites):
        if s["rows"] is None:
            continue
        for ry, cells in enumerate(s["rows"]):
            for rep_y in range(s["step_y"]):
                sy = s["y"] + ry * s["step_y"] + rep_y
                if not (0 <= sy < H):
                    continue
                ox = 0
                for color in cells:
                    if color != TRANSPARENT:
                        for rep_x in range(s["step_x"]):
                            sx = s["x"] + ox + rep_x
                            if 0 <= sx < W:
                                canvas[sy][sx] = color
                    ox += s["step_x"]

    for s in sprites:
        s.pop("rows", None)
        s.pop("step_x", None)
        s.pop("step_y", None)

    hist = [0] * 16
    for row in canvas:
        for c in row:
            hist[c] += 1
    return {"complete": complete, "background": bg, "sprites": sprites, "histogram": hist,
            "colors_used": sum(1 for h in hist if h)}
