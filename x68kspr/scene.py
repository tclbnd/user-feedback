"""X68000 sprite/BG snapshots (.x68s): sprite scroll registers, BG control, PCG palette and PCG/BG RAM ($EB8000)."""
import struct

W = H = 256
HDR = 8 + 1024 + 12 + 512
RAM = 0x8000


class SceneError(ValueError):
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
    return "#%02x%02x%02x" % tuple((c << 3) | (c >> 2) for c in (w >> 10 & 31, w >> 5 & 31, w & 31))


def _pix8(ram, code, x, y):
    b = ram[(code * 32 + y * 4 + x // 2) % RAM]
    return b >> 4 if x & 1 else b & 15


def _pix16(ram, code, x, y):
    q = (y >= 8) * 2 + (x >= 8)
    b = ram[(code * 128 + q * 32 + (y % 8) * 4 + (x % 8) // 2) % RAM]
    return b >> 4 if x & 1 else b & 15


def parse(data):
    if len(data) < HDR or data[:8] != b"X68SPBG\0":
        raise SceneError("not an X68000 sprite/BG snapshot")
    spr = [struct.unpack_from(">HHHH", data, 8 + 8 * n) for n in range(128)]
    bgctl, res, s0x, s0y, s1x, s1y = struct.unpack_from(">HH4H", data, 8 + 1024)
    pal = struct.unpack_from(">256H", data, 8 + 1024 + 12)
    ram = data[HDR:HDR + RAM]
    big = (res & 3) == 1
    out = {"display": bool(bgctl & 0x200), "bg_tile": 16 if big else 8, "complete": len(ram) == RAM,
           "sprites_shown": sum(1 for s in spr if s[3] & 3) if bgctl & 0x200 else 0}
    if not out["complete"]:
        return {**out, "colors_used": None, "sprite_pixels": None, "first_row": None, "row_128": None, "last_row": None}
    # layers: each a 256x256 grid of (palette index or None)
    def bg(area, sx, sy):
        ts = 16 if big else 8
        size = 64 * ts
        base = 0x4000 + 0x2000 * area
        grid = [[None] * W for _ in range(H)]
        for y in range(H):
            for x in range(W):
                mx, my = (x + sx) % size, (y + sy) % size
                w = struct.unpack_from(">H", ram, base + 2 * ((my // ts) * 64 + mx // ts))[0]
                px, py = mx % ts, my % ts
                if w & 0x8000:
                    px = ts - 1 - px
                if w & 0x4000:
                    py = ts - 1 - py
                v = _pix8(ram, w & 255, px % 8, py % 8)
                if v:
                    grid[y][x] = (w >> 8 & 15) * 16 + v
        return grid

    def sprites(prw):
        grid = [[None] * W for _ in range(H)]
        for n in range(127, -1, -1):
            x0, y0, c, p = spr[n]
            if (p & 3) != prw:
                continue
            sx, sy = x0 & 0x3FF, y0 & 0x3FF
            for py in range(16):
                for px in range(16):
                    X, Y = sx + px, sy + py
                    if not (0 <= X < W and 0 <= Y < H):
                        continue
                    tx = 15 - px if c & 0x8000 else px
                    ty = 15 - py if c & 0x4000 else py
                    v = _pix16(ram, c & 255, tx, ty)
                    if v:
                        grid[Y][X] = (c >> 8 & 15) * 16 + v
        return grid

    layers = []
    if bgctl & 0x200:
        layers.append(("s", sprites(1)))
        if bgctl & 0x08:
            layers.append(("b", bg(bgctl >> 4 & 1, s1x & 0x3FF, s1y & 0x3FF)))
        layers.append(("s", sprites(2)))
        if bgctl & 0x01:
            layers.append(("b", bg(bgctl >> 1 & 1, s0x & 0x3FF, s0y & 0x3FF)))
        layers.append(("s", sprites(3)))
    rows, seen, sp = [], set(), 0
    for y in range(H):
        line = []
        for x in range(W):
            col, kind = "#000000", None
            for k, g in layers:
                if g[y][x] is not None:
                    col, kind = _rgb(pal[g[y][x]]), k
            sp += kind == "s"
            line.append(col)
        seen.update(line)
        rows.append(line)
    return {**out, "colors_used": len(seen), "sprite_pixels": sp,
            "first_row": _runs(rows[0]), "row_128": _runs(rows[128]), "last_row": _runs(rows[-1])}
