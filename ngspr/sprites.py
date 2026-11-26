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
    base = tile * 128 + (0x40 if x >= 8 else 0x00) + y * 4
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
    # resolve chains
    sprites = []
    x = y = rows = 0
    for n in range(1, 382):
        y3 = v[0x8200 + n]
        y, rows, x = (0x200 - (y3 >> 7)) & 0x1FF, y3 & 0x3F, v[0x8400 + n] >> 7
        if rows:
            sprites.append((n, x, y, rows))
    back = _rgb(pal[0xFFF])
    rows_out, seen, covered, missing = [], set(), 0, 0
    for n, sx, sy, r in sprites:
        for t in range(min(r, 32)):
            code = v[n * 64 + 2 * t]
            missing += code >= ntiles
    for row in range(H):
        scan = row
        line = [None] * W
        for n, sx, sy, r in reversed(sprites):
            ln = (scan - sy) & 0x1FF
            if r < 0x20 and ln >= r * 16:
                continue
            ti, py = (ln >> 4) & 31, ln & 15
            code = v[n * 64 + 2 * ti]
            attr = v[n * 64 + 2 * ti + 1]
            if attr & 2:
                ti = r - 1 - ti
                py ^= 15
                code = v[n * 64 + 2 * ti]
            p = attr >> 8
            for px in range(16):
                X = (sx + px) & 0x1FF
                if X >= W:
                    continue
                c = _pixel(rom, code, 15 - px if attr & 1 else px, py) if code < ntiles else 0
                if c:
                    line[X] = pal[p * 16 + c]
        covered += sum(1 for c in line if c is not None)
        out = [back if c is None else _rgb(c) for c in line]
        seen.update(out)
        rows_out.append(out)
    return {"sprites": len(sprites), "tiles": ntiles, "missing_tiles": missing, "colors_used": len(seen),
            "sprite_pixels": covered, "first_row": _runs(rows_out[0]), "row_112": _runs(rows_out[112]), "last_row": _runs(rows_out[-1])}
