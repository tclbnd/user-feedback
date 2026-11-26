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


def _tile_offset(lcdc, index):
    """Byte offset of a tile's first row within a VRAM bank."""
    if lcdc & 0x10:                     # $8000 addressing: indexes 0-255
        return index * 16
    if index < 128:                     # $8800 addressing: 0-127 at $9000
        return 0x1000 + index * 16
    return 0x0800 + (index - 128) * 16  # 128-255 are tiles $00-$7F mirrored at $8800


def parse(data):
    if len(data) < 10 + 64 or data[:6] != b"GBCBG\0":
        raise BgError("not a GBC background snapshot")
    lcdc, scx, scy = data[6], data[7], data[8]
    pal = []
    for i in range(32):
        v = struct.unpack_from("<H", data, 10 + 2 * i)[0]
        # BGR555: bits 0-4 red, 5-9 green, 10-14 blue.
        pal.append("#%02x%02x%02x" % tuple(round((v >> s & 31) * 255 / 31) for s in (0, 5, 10)))
    v0 = data[74:74 + 0x2000]
    v1 = data[74 + 0x2000:74 + 0x4000]
    complete = len(v1) == 0x2000
    if not complete:
        return {"lcdc": lcdc, "complete": False, "palettes": pal, "colors_used": None, "palette_pixels": None,
                "first_line": None, "last_line": None}
    map_base = 0x1C00 if lcdc & 0x08 else 0x1800
    seen, ppx, lines = set(), [0] * 8, []
    for sy in range(H):
        y = (sy + scy) & 0xFF            # the 32 x 32 tile map wraps at 256 pixels
        line = []
        for sx in range(W):
            x = (sx + scx) & 0xFF
            m = map_base + (y // 8) * 32 + x // 8
            index = v0[m]
            attr = v1[m]
            p = attr & 0x07                       # palette 0-7
            bank = v1 if attr & 0x08 else v0      # bit 3: tile data bank
            off = _tile_offset(lcdc, index)
            row = 7 - y % 8 if attr & 0x40 else y % 8
            px = 7 - x % 8 if attr & 0x20 else x % 8
            lo, hi = bank[off + 2 * row], bank[off + 2 * row + 1]
            c = (lo >> (7 - px) & 1) | (hi >> (7 - px) & 1) << 1
            col = pal[p * 4 + c]
            ppx[p] += 1
            seen.add(col)
            line.append(col)
        lines.append(line)
    return {"lcdc": lcdc, "complete": True, "palettes": pal, "colors_used": len(seen), "palette_pixels": ppx,
            "first_line": _runs(lines[0]), "last_line": _runs(lines[-1])}
