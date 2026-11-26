"""Neo Geo sprite snapshots (.ngspr): SCB1-SCB4 in VRAM, palette RAM and C ROM tiles."""
import struct

W, H = 320, 224
VRAM_WORDS = 0x8800
HDR = 8 + 2 * VRAM_WORDS + 8192


class SpriteError(ValueError):
    pass


def _runs(line):
    out = []
    for c in line:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


def _rgb(w):
    r = (w >> 8 & 15) << 1 | (w >> 14 & 1)
    g = (w >> 4 & 15) << 1 | (w >> 13 & 1)
    b = (w & 15) << 1 | (w >> 12 & 1)
    return "#%02x%02x%02x" % tuple((c << 3) | (c >> 2) for c in (r, g, b))


def _pixel(rom, tile, x, y):
    # Each 16x16 tile is 128 bytes: the first 64 bytes hold the left
    # 8 columns and the next 64 the right 8 columns.  Every row is four
    # bytes, one per bit plane 0-3; the cartridge bus interleaves the
    # C1 ROM (planes 0,1) on even bytes with C2 (planes 2,3) on odd.
    base = tile * 128 + (0x00 if x < 8 else 0x40) + y * 4
    if base + 4 > len(rom):
        return 0
    bit = 7 - (x & 7)
    b0, b1, b2, b3 = rom[base], rom[base + 1], rom[base + 2], rom[base + 3]
    return (b0 >> bit & 1) | (b1 >> bit & 1) << 1 | (b2 >> bit & 1) << 2 | (b3 >> bit & 1) << 3


def parse(data):
    if len(data) < HDR or data[:8] != b"NGSPR\0\0\0":
        raise SpriteError("not a Neo Geo sprite snapshot")
    v = struct.unpack_from(">%dH" % VRAM_WORDS, data, 8)
    pal = struct.unpack_from(">4096H", data, 8 + 2 * VRAM_WORDS)
    rom = data[HDR:]
    ntiles = len(rom) // 128
    for n in range(1, 382):
        if v[0x8200 + n] & 0x3F and v[0x8000 + n] != 0x0FFF:
            raise SpriteError("shrunk sprites are not supported")
    # Resolve chains.  A sprite with a non-zero height starts a chain; its
    # position registers hold the top-left corner.  A sprite whose height
    # is zero is chained: it keeps the head's y/height and is placed 16
    # pixels to the right of the previous link.  All positions are 9-bit
    # and wrap around the 512-pixel playfield.  An entry with neither a
    # position nor any tile data marks the end of a chain.
    sprites = []
    heads = 0
    head_x = head_y = rows = 0
    chain_x = 0
    for n in range(1, 382):
        y3 = v[0x8200 + n]
        xw = v[0x8400 + n]
        height = y3 & 0x3F
        sy = (0x200 - (y3 >> 7)) & 0x1FF
        sx = (xw >> 7) & 0x1FF
        if height:
            head_x, head_y, rows = sx, sy, height
            chain_x = rows * 16
            heads += 1
            sprites.append((n, head_x, head_y, rows))
        elif y3 == 0 and xw == 0:
            chain_x = 0
        elif v[n * 64] == 0:
            chain_x = 0
        else:
            sprites.append((n, (head_x + chain_x) & 0x1FF, head_y, rows))
            chain_x += 16
    back = _rgb(pal[0xFFF])
    missing = 0
    for n, sx, sy, r in sprites:
        for t in range(min(r, 32)):
            if v[n * 64 + 2 * t] & 0x3FF >= ntiles:
                missing += 1
    rows_out, seen, covered = [], set(), 0
    for row in range(H):
        line = [None] * W
        # Lower sprite numbers have priority, so draw high numbers first
        # and let lower numbers overwrite them.
        for n, sx, sy, r in reversed(sprites):
            # Sequential position of this scan line inside the sprite on the
            # 512-pixel playfield.  A sprite crossing the playfield wrap
            # reappears on the opposite screen edge with the same index.
            k = (row - sy) & 0x1FF
            if r < 0x20 and k >= r * 16:
                continue
            ti, py = (k >> 4) & 31, k & 15
            code = v[n * 64 + 2 * ti] & 0x3FF
            attr = v[n * 64 + 2 * ti + 1]
            # Flip bits affect the individual 16x16 tile only.
            if attr & 2:
                py ^= 15
            p = (attr >> 8) & 0x7F
            if code >= ntiles:
                continue
            for px in range(16):
                X = (sx + px) & 0x1FF
                if X >= W:
                    continue
                c = _pixel(rom, code, 15 - px if attr & 1 else px, py)
                if c:
                    line[X] = pal[p * 16 + c]
        covered += sum(1 for c in line if c is not None)
        out = [back if c is None else _rgb(c) for c in line]
        seen.update(out)
        rows_out.append(out)
    return {"sprites": heads, "tiles": ntiles, "missing_tiles": missing, "colors_used": len(seen),
            "sprite_pixels": covered, "first_row": _runs(rows_out[0]), "row_112": _runs(rows_out[112]), "last_row": _runs(rows_out[-1])}
