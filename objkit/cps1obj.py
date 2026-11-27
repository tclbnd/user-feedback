"""CPS-1 object (sprite) table snapshots (.cpo): walk the OBJ RAM list and render the sprite layer."""
import zlib

MAGIC = b"CPS1OBJ\0"
X0, X1, Y0, Y1 = 64, 448, 16, 240
EMPTY = 0xFFFF


class ObjError(ValueError):
    pass


def parse(data):
    if len(data) < 8 or data[:8] != MAGIC:
        raise ObjError("not a CPS-1 object snapshot")
    if len(data) < 8 + 2048 + 128 or (len(data) - 8 - 2048) % 128:
        raise ObjError("snapshot must hold 2 KB of OBJ RAM and whole 16x16 tiles")
    obj = data[8:8 + 2048]
    rom = data[8 + 2048:]
    ntiles = len(rom) // 128
    words = [obj[i] | obj[i + 1] << 8 for i in range(0, 2048, 2)]
    marker = None
    for k in range(256):
        if words[k * 4 + 3] == 0xFF00:
            marker = k
            break
    count = 256 if marker is None else marker
    pic = [[EMPTY] * (X1 - X0) for _ in range(Y1 - Y0)]

    def tile(code, col, fx, fy, sx, sy):
        if not 0 <= code < ntiles:
            return 0
        n = 0
        base = code * 128
        for ty in range(16):
            py = sy + ty
            if not Y0 <= py < Y1:
                continue
            ry = 15 - ty if fy else ty
            for tx in range(16):
                px = sx + tx
                if not X0 <= px < X1:
                    continue
                rx = 15 - tx if fx else tx
                o = base + ry * 8 + (rx >> 3) * 4
                b = 7 - (rx & 7)
                pen = (rom[o] >> b & 1) << 3 | (rom[o + 1] >> b & 1) << 2 | (rom[o + 2] >> b & 1) << 1 | (rom[o + 3] >> b & 1)
                if pen != 0:
                    pic[py - Y0][px - X0] = col * 16 + pen
                    n += 1
        return n

    drawn = 0
    tiles = 0
    for k in range(count):
        x, y, code, colour = words[k * 4:k * 4 + 4]
        col = colour & 0x1F
        fx, fy = colour >> 5 & 1, colour >> 6 & 1
        hit = 0
        if colour & 0xFF00:
            nx = (colour >> 8 & 0xF) + 1
            ny = (colour >> 12 & 0xF) + 1
            for nys in range(ny):
                sy = y + nys * 16
                for nxs in range(nx):
                    sx = x + nxs * 16
                    c = code + nys * nx + nxs
                    t = tile(c, col, fx, fy, sx, sy)
                    tiles += t > 0
                    hit += t
        else:
            t = tile(code, col, fx, fy, x, y)
            tiles += t > 0
            hit += t
        drawn += hit > 0
    flat = [v for r in pic for v in r]
    used = [(i % (X1 - X0), i // (X1 - X0)) for i, v in enumerate(flat) if v != EMPTY]
    return {"entries": count, "end_marker": marker, "sprites_drawn": drawn, "tiles_drawn": tiles,
            "pixels": len(used), "colours_used": len(set(v for v in flat if v != EMPTY)),
            "bbox": [min(p[0] for p in used), min(p[1] for p in used), max(p[0] for p in used), max(p[1] for p in used)] if used else None,
            "picture_crc32": "%08x" % zlib.crc32(b"".join(v.to_bytes(2, "little") for v in flat))}
