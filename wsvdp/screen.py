"""WonderSwan / WonderSwan Color display snapshots (.wsv): ports, VRAM, SCR1, SCR2 and sprites."""
import struct

W, H = 224, 144
HDR = 8 + 64 + 2


class WsError(ValueError):
    pass


def _runs(line):
    out = []
    for c in line:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


def parse(data):
    if len(data) < HDR or data[:8] != b"WSVDP\0\0\0":
        raise WsError("not a WonderSwan display snapshot")
    io, p60 = data[8:72], data[72]
    vram = data[HDR:HDR + 0x10000]
    colour, c16, packed = bool(p60 & 0x80), bool(p60 & 0x40) and bool(p60 & 0x80), bool(p60 & 0x20) and bool(p60 & 0x80)
    need = 0x10000 if colour else 0x4000
    if len(vram) < need:
        raise WsError("VRAM is truncated")
    if packed and not c16:
        raise WsError("packed 4-colour tiles are not used")
    mode = "mono" if not colour else ("colour-16-packed" if packed else "colour-16" if c16 else "colour-4")

    def pix(number, bank, line, x):
        if c16:
            base = (0x8000 if bank else 0x4000) + number * 32 + line * 4
            if packed:
                # packed tiles hold two 4-bit pixels per byte, high nibble first
                b = vram[base + x // 2]
                return b >> 4 if x % 2 == 0 else b & 15
            return sum((vram[base + p] >> (7 - x) & 1) << p for p in range(4))
        base = 0x2000 + number * 16 + line * 2
        return (vram[base] >> (7 - x) & 1) | (vram[base + 1] >> (7 - x) & 1) << 1

    def rgb(pal, c, sprite=False):
        if colour:
            # background palettes live at $FE00, sprite palettes at $FF00
            base = 0xFF00 if sprite else 0xFE00
            w = struct.unpack_from("<H", vram, base + 2 * (pal * 16 + c))[0]
            return "#%02x%02x%02x" % ((w >> 8 & 15) * 17, (w >> 4 & 15) * 17, (w & 15) * 17)
        # mono palette words pack four 3-bit shade-pool indexes
        pw = struct.unpack_from("<H", io, 0x20 + 2 * pal)[0]
        k = pw >> (3 * c) & 7
        s = io[0x1C + k // 2] >> (4 * (1 - (k & 1))) & 15
        # shade 0 is the brightest level, shade 15 the darkest
        v = (15 - s) * 17
        return "#%02x%02x%02x" % (v, v, v)

    def opaque(pal, c):
        return c != 0

    mask = 15 if colour else 7
    if colour:
        bgc = io[1]
        back = rgb(bgc >> 4, bgc & 15)
    else:
        s = io[0x1C + (io[1] & 7) // 2] >> (4 * (1 - (io[1] & 1))) & 15
        v = (15 - s) * 17
        back = "#%02x%02x%02x" % (v, v, v)

    def layer(scr):
        # each screen map occupies 2 KB: 32 x 32 entries of 16 bits
        base = (io[7] >> (4 * scr) & mask) * 0x800
        sx, sy = io[0x10 + 2 * scr], io[0x11 + 2 * scr]
        grid = [[None] * W for _ in range(H)]
        for y in range(H):
            yy = (y + sy) & 255
            for x in range(W):
                xx = (x + sx) & 255
                e = struct.unpack_from("<H", vram, base + 2 * ((yy >> 3) * 32 + (xx >> 3)))[0]
                px, py = xx & 7, yy & 7
                if e & 0x2000:
                    px = 7 - px
                if e & 0x4000:
                    py = 7 - py
                pal = e >> 9 & 15
                c = pix(e & 0x1FF, e >> 15 & 1, py, px)
                if opaque(pal, c):
                    grid[y][x] = rgb(pal, c)
        return grid

    def sprites(pri):
        grid = [[None] * W for _ in range(H)]
        tbase = (io[4] & (0x3F if colour else 0x1F)) * 0x200
        first, count = io[5] & 0x7F, io[6]
        # lower-numbered sprites are in front, so draw them last
        for i in range(min(first + count, 128) - 1, first - 1, -1):
            if i < 0:
                break
            w, y0, x0 = struct.unpack_from("<HBB", vram, tbase + 4 * i)
            if bool(w & 0x8000) != pri:
                continue
            pal = w >> 9 & 7
            for ly in range(8):
                for lx in range(8):
                    X, Y = (x0 + lx) & 255, (y0 + ly) & 255
                    if X >= W or Y >= H:
                        continue
                    c = pix(w & 0x1FF, 0, 7 - ly if w & 0x4000 else ly,
                            7 - lx if w & 0x2000 else lx)
                    if opaque(pal, c):
                        grid[Y][X] = rgb(pal, c, sprite=True)
        return grid

    layers = []
    if io[0] & 1:
        layers.append(("b", layer(0)))
    if io[0] & 4:
        layers.append(("s", sprites(False)))
    if io[0] & 2:
        layers.append(("b", layer(1)))
    if io[0] & 4:
        layers.append(("s", sprites(True)))
    rows, seen, sp = [], set(), 0
    for y in range(H):
        line = []
        for x in range(W):
            col, kind = back, None
            for k, g in layers:
                if g[y][x] is not None:
                    col, kind = g[y][x], k
            sp += kind == "s"
            line.append(col)
        seen.update(line)
        rows.append(line)
    return {"mode": mode, "colors_used": len(seen), "sprite_pixels": sp,
            "first_row": _runs(rows[0]), "row_72": _runs(rows[72]), "last_row": _runs(rows[-1])}
