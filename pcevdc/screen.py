"""PC Engine VDC snapshots (.pcev): HuC6270 registers, VRAM, sprite attribute table and HuC6260 colour table."""
import struct

W, H = 256, 224
HDR = 8 + 8
SIZE = HDR + 0x10000 + 512 + 1024


class VdcError(ValueError):
    pass


def _runs(line):
    out = []
    for c in line:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


def _rgb(v):
    p3 = lambda c: (c << 5) | (c << 2) | (c >> 1)
    return "#%02x%02x%02x" % (p3(v >> 6 & 7), p3(v >> 3 & 7), p3(v & 7))


def parse(data):
    if len(data) < HDR or data[:8] != b"PCEVDC\0\0":
        raise VdcError("not a PC Engine VDC snapshot")
    if len(data) < SIZE:
        raise VdcError("snapshot is truncated")
    mwr, bxr, byr, cr = struct.unpack_from("<HHHH", data, 8)
    vram = struct.unpack_from("<32768H", data, HDR)
    sat = struct.unpack_from("<256H", data, HDR + 0x10000)
    vce = struct.unpack_from("<512H", data, HDR + 0x10000 + 512)
    width = 32
    height = 32
    bg_on, spr_on = bool(cr & 0x80), bool(cr & 0x40)
    bxr, byr = bxr & 0x3FF, byr & 0x1FF

    # sprite line buffers: per screen line, x -> (entry, front)
    sprites = [dict() for _ in range(H)]
    shown = 0
    if spr_on:
        for n in range(64):
            y0, x0, pw, at = sat[4 * n] & 0x3FF, sat[4 * n + 1] & 0x3FF, sat[4 * n + 2] & 0x7FF, sat[4 * n + 3]
            cgx, cgy = at >> 8 & 1, at >> 12 & 3
            h = (16, 32, 64, 64)[cgy]
            wpx = 32 if cgx else 16
            pattern = pw >> 1
            sx, sy = x0 - 32, y0 - 64
            if sx >= W or sy >= H or sx + wpx <= 0 or sy + h <= 0:
                continue
            shown += 1
            pal, front, xf, yf = at & 15, bool(at & 0x80), bool(at & 0x800), bool(at & 0x8000)
            for ly in range(h):
                Y = sy + ly
                if not 0 <= Y < H:
                    continue
                line = (h - 1 - ly) if yf else ly
                for cx in range(wpx // 16):
                    cell = pattern + (line >> 4) * (wpx // 16) + cx
                    base = (cell << 6) + (line & 15)
                    for px in range(16):
                        X = sx + 16 * cx + px
                        if not 0 <= X < W or X in sprites[Y]:
                            continue
                        bit = 15 - ((15 - px) if xf else px)
                        c = sum(((vram[(base + 16 * p) & 0x7FFF] >> bit) & 1) << p for p in range(4))
                        if c:
                            sprites[Y][X] = (pal << 4 | c, front)
    rows, seen, sp = [], set(), 0
    for y in range(H):
        line = []
        by = (byr + y) & (height * 8 - 1)
        for x in range(W):
            bx = (bxr + x) & (width * 8 - 1)
            entry = 0x100
            if bg_on:
                e = vram[(by >> 3) * width + (bx >> 3)]
                base = ((e & 0xFFF) << 4) + (by & 7)
                w0, w1 = vram[base & 0x7FFF], vram[(base + 8) & 0x7FFF]
                bit = 7 - (bx & 7)
                c = (w0 >> bit & 1) | (w0 >> (bit + 8) & 1) << 1 | (w1 >> bit & 1) << 2 | (w1 >> (bit + 8) & 1) << 3
                entry = (e >> 12) << 4 | c
            s = sprites[y].get(x)
            if s and (s[1] or not bg_on or entry & 15 == 0):
                entry = s[0]
                sp += 1
            line.append(_rgb(vce[entry] & 0x1FF))
        seen.update(line)
        rows.append(line)
    return {"bat_width": width, "bat_height": height, "background": bg_on, "sprites_on": spr_on, "sprites_shown": shown,
            "colors_used": len(seen), "sprite_pixels": sp,
            "first_row": _runs(rows[0]), "row_112": _runs(rows[112]), "last_row": _runs(rows[-1])}
