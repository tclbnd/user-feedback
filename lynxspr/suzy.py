"""Atari Lynx sprite snapshots (.lnxs): Suzy SCB chain, sprite data lines and the Mikey palette."""
import struct

W, H = 160, 102
HDR = 8 + 2 + 32


class SuzyError(ValueError):
    pass


def _runs(line):
    out = []
    for c in line:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


class _Bits:
    def __init__(self, data):
        self.d, self.p = data, 0

    def left(self):
        return len(self.d) * 8 - self.p

    def take(self, n):
        v = 0
        for _ in range(n):
            v = v << 1 | (self.d[self.p >> 3] >> (7 - (self.p & 7)) & 1)
            self.p += 1
        return v


def _line(data, bpp, literal):
    b = _Bits(data)
    out = []
    if literal:
        while b.left() >= bpp:
            out.append(b.take(bpp))
        return out
    while b.left() >= 5:
        lit, count = b.take(1), b.take(4)
        if lit:
            for _ in range(count + 1):
                if b.left() < bpp:
                    return out
                out.append(b.take(bpp))
        else:
            if b.left() < bpp:
                return out
            out += [b.take(bpp)] * (count + 1)
    return out


def parse(data):
    if len(data) < HDR + 0x10000 or data[:8] != b"LYNXSCB\0":
        raise SuzyError("not a complete Lynx sprite snapshot")
    head = struct.unpack_from("<H", data, 8)[0]
    green, br = data[10:26], data[26:42]
    ram = data[HDR:HDR + 0x10000]
    u16 = lambda a: ram[a & 0xFFFF] | ram[(a + 1) & 0xFFFF] << 8
    s16 = lambda a: u16(a) - 0x10000 if u16(a) & 0x8000 else u16(a)
    fb = [[0] * W for _ in range(H)]
    pens = list(range(16))
    addr, drawn, skipped, guard = head, 0, 0, 0
    while addr & 0xFF00:
        guard += 1
        if guard > 256:
            raise SuzyError("SCB chain does not end")
        ctl0, ctl1 = ram[addr], ram[(addr + 1) & 0xFFFF]
        nxt = u16(addr + 3)
        if ctl1 & 0x04:
            skipped += 1
            addr = nxt
            continue
        reload = ctl1 >> 4 & 3
        if reload and (u16(addr + 0x0B) != 0x100 or u16(addr + 0x0D) != 0x100):
            raise SuzyError("scaled sprites are not supported")
        if ctl1 & 3:
            raise SuzyError("draw origins other than down/right are not supported")
        bpp = (ctl0 >> 6) + 1
        if not ctl1 & 0x08:
            off = 0x0F
            for i in range((1 << bpp) // 2):
                b = ram[(addr + off + i) & 0xFFFF]
                pens[2 * i], pens[2 * i + 1] = b & 15, b >> 4
        kind = ctl0 & 7
        xdir = 1
        ydir = 1
        hpos, vpos = s16(addr + 7), s16(addr + 9)
        p, line = u16(addr + 5), 0
        while True:
            off = ram[p & 0xFFFF]
            if off in (0, 1):
                break
            pix = _line(bytes(ram[(p + 1 + i) & 0xFFFF] for i in range(off - 1)), bpp, not ctl1 & 0x80)
            y = vpos + line * ydir
            for i, v in enumerate(pix):
                x = hpos + i * xdir
                if not (0 <= x < W and 0 <= y < H):
                    continue
                pen = pens[v]
                if kind in (0, 1):
                    fb[y][x] = pen
                elif kind in (2, 3):
                    if pen:
                        fb[y][x] = pen
                elif kind == 6:
                    if pen:
                        fb[y][x] ^= pen
                elif pen:
                    fb[y][x] = pen
            p += off
            line += 1
        drawn += 1
        addr = nxt
    rgb = ["#%02x%02x%02x" % ((br[i] >> 4) * 17, (green[i] & 15) * 17, (br[i] & 15) * 17) for i in range(16)]
    rows = [[rgb[c] for c in r] for r in fb]
    counts = [sum(r.count(i) for r in fb) for i in range(16)]
    return {"sprites_drawn": drawn, "sprites_skipped": skipped, "pen_pixels": counts,
            "colors_used": len({c for r in rows for c in r}),
            "first_row": _runs(rows[0]), "row_51": _runs(rows[51]), "last_row": _runs(rows[-1])}
