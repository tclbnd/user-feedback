"""Super NES BG1 snapshots (.sbg): PPU registers, CGRAM and VRAM."""
import struct

W, H, HDR = 256, 224, 16
BPP = {0: 2, 1: 4, 3: 8}

# BG1 tilemap entry bits
FLIP_X, FLIP_Y = 0x4000, 0x8000


class LayerError(ValueError):
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
    # CGRAM word: bits 0-4 red, 5-9 green, 10-14 blue (BGR layout)
    return "#%02x%02x%02x" % tuple(round((w >> s & 31) * 255 / 31) for s in (0, 5, 10))


def _vram_word(vram, byte_addr):
    return vram[byte_addr & 0xFFFF] | (vram[(byte_addr + 1) & 0xFFFF] << 8)


def _map_addr(mapbase, wide, tall, cx, cy):
    # 32x32-cell screens; two screens sit side by side (wide) or stacked (tall)
    screen = (cy >> 5) * (1 + wide) + (cx >> 5)
    return mapbase + screen * 0x800 + ((cy & 31) * 32 + (cx & 31)) * 2


def _pixel(vram, charbase, bpp, tile, px, py):
    # One 8x8 tile: bitplanes interleaved in pairs, each plane 8 contiguous
    # bytes, so plane p occupies tile offset p*8 and row y is at p*8 + y.
    base = charbase + tile * 8 * bpp
    c = 0
    for plane in range(bpp):
        off = base + plane * 8 + py
        c |= ((vram[off & 0xFFFF] >> (7 - px)) & 1) << plane
    return c


def parse(data):
    if len(data) < HDR + 512 or data[:8] != b"SNESBG\0\0":
        raise LayerError("not a Super NES BG snapshot")
    bgmode, sc, nba, hofs, vofs = struct.unpack_from("<BBBxHH", data, 8)
    cg = struct.unpack_from("<256H", data, HDR)
    vram = data[HDR + 512:HDR + 512 + 0x10000]
    mode = bgmode & 7
    if mode not in BPP:
        raise LayerError("BG mode %d is not supported" % mode)
    big = bool(bgmode & 0x10)
    bpp = BPP[mode]
    out = {"mode": mode, "bpp": bpp, "tile_size": 16 if big else 8, "complete": len(vram) == 0x10000}
    if not out["complete"]:
        return {**out, "colors_used": None, "transparent_pixels": None, "palette_pixels": None, "first_row": None, "last_row": None}
    # BG1SC bits 7:2 pick the map base in 2 KB units; BG12NBA in 8 KB units
    mapbase = (sc >> 2) * 0x800
    wide, tall = sc & 1, (sc >> 1) & 1
    charbase = (nba & 15) * 0x2000
    mw, mh = 256 * (1 + wide), 256 * (1 + tall)
    back = _rgb(cg[0])
    seen, ppx, lines, clear = set(), [0] * 8, [], 0
    for sy in range(H):
        line = []
        for sx in range(W):
            x, y = (sx + hofs) % mw, (sy + vofs) % mh
            if big:
                # 16x16 mode: every 2x2 group of cells shares the top-left
                # entry; the hardware sets only the low bit of the tile number
                # for the other three subtiles.
                gx, gy = (x // 8) & ~1, (y // 8) & ~1
                e = _vram_word(vram, _map_addr(mapbase, wide, tall, gx, gy))
                tile0, pal = e & 0x3FF, (e >> 10) & 7
                sx8, sy8 = (x // 8) & 1, (y // 8) & 1
                tile = (tile0 & 0x3FE) | (sx8 | (sy8 << 1))
                px, py = x & 7, y & 7
                if e & FLIP_X:
                    px = 7 - px
                if e & FLIP_Y:
                    py = 7 - py
            else:
                cx, cy = x // 8, y // 8
                e = _vram_word(vram, _map_addr(mapbase, wide, tall, cx, cy))
                tile, pal = e & 0x3FF, (e >> 10) & 7
                px, py = x & 7, y & 7
                if e & FLIP_X:
                    px = 7 - px
                if e & FLIP_Y:
                    py = 7 - py
            c = _pixel(vram, charbase, bpp, tile, px, py)
            if c == 0:
                col = back
                clear += 1
            else:
                if mode == 3:
                    # 8bpp BG: pixel indexes CGRAM directly, palette field unused
                    cg_index = c
                elif mode == 0:
                    # 2bpp BG: one 4-colour palette every 8 CGRAM entries
                    cg_index = pal * 8 + c
                else:
                    cg_index = pal * 16 + c
                col = _rgb(cg[cg_index])
                ppx[pal] += 1
            seen.add(col)
            line.append(col)
        lines.append(line)
    return {**out, "colors_used": len(seen), "transparent_pixels": clear, "palette_pixels": ppx,
            "first_row": _runs(lines[0]), "last_row": _runs(lines[-1])}
