"""Game Boy Color background snapshots (.gbcbg): LCDC, scroll, BG palettes, VRAM banks 0 and 1."""
import struct

W, H = 160, 144


class BgError(ValueError):
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
    if len(data) < 10 + 64 or data[:6] != b"GBCBG\0":
        raise BgError("not a GBC background snapshot")
    lcdc, scx, scy = data[6], data[7], data[8]
    pal = []
    for i in range(32):
        v = struct.unpack_from("<H", data, 10 + 2 * i)[0]
        pal.append("#%02x%02x%02x" % tuple(round((v >> s & 31) * 255 / 31) for s in (10, 5, 0)))
    v0 = data[74:74 + 0x2000]
    v1 = data[74 + 0x2000:74 + 0x4000]
    complete = len(v1) == 0x2000
    if not complete:
        return {"lcdc": lcdc, "complete": False, "palettes": pal, "colors_used": None, "palette_pixels": None,
                "first_line": None, "last_line": None}
    seen, ppx, lines = set(), [0] * 8, []
    for sy in range(H):
        line = []
        for sx in range(W):
            x, y = sx + scx, sy + scy
            if x > 255 or y > 255:
                col = pal[0]
            else:
                idx = v0[0x1800 + (y // 8) * 32 + x // 8]
                base = idx * 16
                lo, hi = v0[base + 2 * (y % 8)], v0[base + 2 * (y % 8) + 1]
                c = (lo >> (7 - x % 8) & 1) | (hi >> (7 - x % 8) & 1) << 1
                col = pal[c]
            ppx[0] += 1
            seen.add(col)
            line.append(col)
        lines.append(line)
    return {"lcdc": lcdc, "complete": True, "palettes": pal, "colors_used": len(seen), "palette_pixels": ppx,
            "first_line": _runs(lines[0]), "last_line": _runs(lines[-1])}
