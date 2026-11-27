"""Galaxian-hardware screen snapshots (.gxs): background tiles with column scroll, sprites and bullets."""
import zlib

MAGIC = b"GALAXSNP"
TOP, BOTTOM = 16, 240
SHELL, MISSILE = 32, 33


class GxError(ValueError):
    pass


def parse(data):
    if len(data) < 8 or data[:8] != MAGIC:
        raise GxError("not a Galaxian screen snapshot")
    if len(data) != 8 + 1024 + 256 + 4096:
        raise GxError("snapshot must hold video RAM, object RAM and the graphics ROM")
    vram = data[8:1032]
    obj = data[1032:1288]
    gfx = data[1288:]
    p0, p1 = gfx[:2048], gfx[2048:]
    pic = [[0] * 256 for _ in range(256)]

    def pen(off, x):
        b = 7 - x
        return (p0[off] >> b & 1) | (p1[off] >> b & 1) << 1

    tiles = 0
    for col in range(32):
        scroll = obj[col * 2 + 1]
        colour = obj[col * 2] & 7
        for y in range(TOP, BOTTOM):
            sy = (y - scroll) & 0xFF
            code = vram[(sy >> 3) * 32 + col]
            for x in range(8):
                p = pen(code * 8 + (sy & 7), x)
                if p:
                    pic[y][col * 8 + x] = colour * 4 + p
    for row in range(32):
        for col in range(32):
            code = vram[row * 32 + col]
            if any(gfx[code * 8 + r] or gfx[2048 + code * 8 + r] for r in range(8)):
                tiles += 1

    boxes = []
    for num in range(8):
        base = obj[0x40 + num * 4:0x44 + num * 4]
        sy = base[0]
        sx = base[3]
        code = base[1]
        fx, fy = base[1] & 0x40, base[1] & 0x80
        colour = base[2] & 7
        drawn = 0
        for yy in range(16):
            py = sy + yy
            if not TOP <= py < BOTTOM:
                continue
            ry = 15 - yy if fy else yy
            for xx in range(16):
                px = sx + xx
                if not 0 <= px < 256:
                    continue
                rx = 15 - xx if fx else xx
                p = pen((code * 32 + ry + (rx >> 3) * 16) & 2047, rx & 7)
                if p:
                    pic[py][px] = colour * 4 + p
                    drawn += 1
        if drawn:
            boxes.append([num, sx, sy - TOP])
    boxes.sort()

    bullet_px = 0
    for y in range(TOP, BOTTOM):
        shell = missile = None
        for w in range(8):
            if obj[0x60 + w * 4 + 1] == y:
                if w != 7:
                    shell = w
                else:
                    missile = w
        for w, c in ((shell, SHELL), (missile, SHELL)):
            if w is None:
                continue
            x0 = obj[0x60 + w * 4 + 3]
            for x in range(x0, x0 + 2):
                if 0 <= x < 256:
                    pic[y][x] = c
    rows = pic[TOP:BOTTOM]
    flat = bytes(v for r in rows for v in r)
    bullet_px = sum(1 for v in flat if v >= SHELL)
    return {"tiles_used": tiles, "column_colours": [obj[c * 2 + 1] & 7 for c in range(32)],
            "sprites": boxes, "bullet_pixels": bullet_px, "colours_used": len(set(flat)),
            "picture_crc32": "%08x" % zlib.crc32(flat)}
