"""Super NES BG1 snapshots (.sbg): PPU registers, CGRAM and VRAM."""
import struct

W, H, HDR = 256, 224, 16
BPP = {0: 2, 1: 4, 3: 8}


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
    return "#%02x%02x%02x" % tuple(round((w >> s & 31) * 255 / 31) for s in (10, 5, 0))


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
    mapbase = (sc >> 2) * 0x400
    wide, tall = sc & 1, sc >> 1 & 1
    charbase = (nba & 15) * 0x1000
    mw, mh = 256 * (1 + wide), 256 * (1 + tall)
    back = _rgb(cg[0])
    seen, ppx, lines, clear = set(), [0] * 8, [], 0
    for sy in range(H):
        line = []
        for sx in range(W):
            x, y = (sx + hofs) % mw, (sy + vofs) % mh
            cx, cy = x // 8, y // 8
            e = struct.unpack_from("<H", vram, mapbase + 2 * (cy * 32 * (1 + wide) + cx))[0]
            t, pal = e & 0x3FF, e >> 10 & 7
            px, py = x % 8, y % 8
            base = charbase + t * 8 * bpp
            c = 0
            for p in range(bpp):
                c |= (vram[(base + 2 * py * (bpp // 2) + p) & 0xFFFF] >> (7 - px) & 1) << p
            if c == 0:
                col = back
                clear += 1
            else:
                col = _rgb(cg[(pal * 16 + c) & 255])
                ppx[pal] += 1
            seen.add(col)
            line.append(col)
        lines.append(line)
    return {**out, "colors_used": len(seen), "transparent_pixels": clear, "palette_pixels": ppx,
            "first_row": _runs(lines[0]), "last_row": _runs(lines[-1])}
