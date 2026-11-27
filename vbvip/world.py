"""Virtual Boy VIP world snapshots (.vbw): brightness registers, BG palettes, one BG world, characters, BG map."""
import struct

SW, SH = 384, 224
HDR = 6 + 8 + 12

# BG map control word bits
F_HFLIP = 0x2000  # bit 13: mirror the character horizontally
F_VFLIP = 0x1000  # bit 12: mirror the character vertically
F_CHAR = 0x01FF   # bits 8-0: character number
F_PALETTE = 0xC000  # bits 15-14: GPLT register number
MAPSIZE = 64      # BG map is 64 x 64 characters = 512 x 512 pixels


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
    # BRTA/BRTB/BRTC are the per-step pulse widths of shades 1/2/3, so the
    # LED level of each shade is the sum of its register and every lower one.
    level = [0, brta, brta + brtb, brta + brtb + brtc]
    # The WAT W/H fields hold (width - 1)/(height - 1), in pixels.
    w, h = w + 1, h + 1
    chars = data[HDR:HDR + 2048 * 16]
    bmap = data[HDR + 2048 * 16:HDR + 2048 * 16 + 8192]
    complete = len(bmap) == 8192
    # BKCOL selects the shade painted where no character pixel is drawn.
    bg = level[bkcol & 3]
    out = {"window": [gx, gy, w, h], "levels": level, "background": bg, "complete": complete,
           "screen": None, "first_line": None, "last_line": None}
    if not complete:
        return out

    def px(x, y):
        # The 64 x 64 BG map wraps at 512 pixels in each direction.
        u, v = (mx + x) & 511, (my + y) & 511
        cw = struct.unpack_from("<H", bmap, 2 * ((v >> 3) * MAPSIZE + (u >> 3)))[0]
        ch = cw & F_CHAR
        tx, ty = u & 7, v & 7
        if cw & F_HFLIP:
            tx = 7 - tx
        if cw & F_VFLIP:
            ty = 7 - ty
        # A character is 8 rows of 16 bits: plane 0 then plane 1, MSB first.
        base = ch * 16 + 2 * ty
        plane0, plane1 = chars[base], chars[base + 1]
        bit = 7 - tx
        idx = ((plane0 >> bit) & 1) | (((plane1 >> bit) & 1) << 1)
        if idx == 0:
            # Index 0 is transparent: the background colour shows through.
            return bg
        # GPLT fields are 2 bits each, field n holding the shade of pixel n.
        shade = (gplt[cw >> 14] >> (2 * idx)) & 3
        return level[shade]

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
