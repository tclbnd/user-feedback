"""Pokemon mini PRC snapshots (.pmp): map and sprite rendering into the frame buffer, then the LCD copy."""

HDR = 8 + 16
MEM = 0x20000
LIGHT, DARK = "#fffb87", "#4e4e4e"


class PrcError(ValueError):
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
    if len(data) < HDR or data[:8] != b"PMPRC\0\0\0":
        raise PrcError("not a Pokemon mini PRC snapshot")
    regs = data[8:24]
    mem = bytearray(data[HDR:HDR + MEM])
    if len(mem) < MEM:
        raise PrcError("memory image is truncated")
    mode = regs[0]
    width = 12
    bg = (regs[2] & 0xF8) | regs[3] << 8 | (regs[4] & 0x1F) << 16
    spr = (regs[7] & 0xC0) | regs[8] << 8 | (regs[9] & 0x1F) << 16
    rd = lambda a: mem[a] if a < MEM else 0
    fb = 0x1000
    for k in range(0x300):
        mem[fb + k] = 0
    if mode & 0x02:
        for y in range(8):
            for x in range(12):
                tile = mem[0x1360 + y * width + x]
                for i in range(8):
                    mem[fb + y * 96 + x * 8 + i] = rd(bg + tile * 8 + i)
    shown = 0
    if mode & 0x04:
        for e in range(24):
            a = 0x1300 + 4 * e
            sx, sy = mem[a] & 0x7F, mem[a + 1] & 0x7F
            tile, flag = mem[a + 2], mem[a + 3]
            if not flag & 0x08:
                continue
            shown += 1
            base = spr + tile * 64
            for i in range(16):
                rx = 15 - i if flag & 1 else i
                s = base + ((rx & 8) << 1) + (rx & 7)
                mask = (rd(s + 32) | rd(s + 40) << 8) & 0xFFFF
                gfx = rd(s) | rd(s + 8) << 8
                if flag & 4:
                    gfx, mask = ~gfx & 0xFFFF, ~mask & 0xFFFF
                for j in range(16):
                    bit = (0x8000 >> j) if flag & 2 else (1 << j)
                    X, Y = sx + i, sy + j
                    if 0 <= X < 96 and 0 <= Y < 64 and mask & bit:
                        addr = fb + (Y >> 3) * 96 + X
                        if gfx & bit:
                            mem[addr] |= 1 << (Y & 7)
                        else:
                            mem[addr] &= ~(1 << (Y & 7)) & 0xFF
    rows = []
    for y in range(64):
        if mode & 0x08:
            rows.append([DARK if mem[fb + (y >> 3) * 96 + x] >> (7 - (y & 7)) & 1 else LIGHT for x in range(96)])
        else:
            rows.append([LIGHT] * 96)
    dark = sum(r.count(DARK) for r in rows)
    return {"map_width": width, "sprites_shown": shown, "dark_pixels": dark,
            "first_row": _runs(rows[0]), "row_32": _runs(rows[32]), "last_row": _runs(rows[-1])}
