"""Virtual Boy VIP world snapshots (.vbw): brightness registers, BG palettes, one BG world, characters, BG map."""
import struct

SW, SH = 384, 224
HDR = 6 + 8 + 12


class WorldError(ValueError):
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
    if len(data) < HDR or data[:6] != b"VBVIP\0":
        raise WorldError("not a Virtual Boy world snapshot")
    brta, brtb, brtc, bkcol = data[6:10]
    gplt = data[10:14]
    gx, gy, mx, my, w, h = struct.unpack_from("<hhhhHH", data, 14)
    level = [0, brta, brtb, brtc]
    chars = data[HDR:HDR + 2048 * 16]
    bmap = data[HDR + 2048 * 16:HDR + 2048 * 16 + 8192]
    complete = len(bmap) == 8192
    out = {"window": [gx, gy, w, h], "levels": level, "background": level[bkcol & 3], "complete": complete,
           "screen": None, "first_line": None, "last_line": None}
    if not complete:
        return out
    bg = level[bkcol & 3]

    def px(x, y):
        u, v = (mx + x) % 256, (my + y) % 256
        cw = struct.unpack_from("<H", bmap, 2 * ((v >> 3) * 64 + (u >> 3)))[0]
        ch = cw & 0x1FF
        tx, ty = u & 7, v & 7
        if cw & 0x1000:
            tx = 7 - tx
        if cw & 0x2000:
            ty = 7 - ty
        hw = struct.unpack_from(">H", chars, ch * 16 + 2 * ty)[0]
        idx = hw >> (14 - 2 * tx) & 3
        return level[gplt[cw >> 14] >> (2 * idx - 2) & 3] if idx else 0

    hist = {}
    for sy in range(SH):
        for sx in range(SW):
            x, y = sx - gx, sy - gy
            c = px(x, y) if 0 <= x < w and 0 <= y < h else bg
            hist[c] = hist.get(c, 0) + 1
    out["screen"] = {str(k): hist[k] for k in sorted(hist)}
    out["first_line"] = _runs([px(x, 0) for x in range(w)])
    out["last_line"] = _runs([px(x, h - 1) for x in range(w)])
    return out
