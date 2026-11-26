"""Atari 7800 MARIA snapshots (.a78s): registers $20-$3F and memory; 160A objects drawn from the display list list."""

LINES, WIDTH, HDR = 243, 160, 8 + 32


class MariaError(ValueError):
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
    if len(data) < HDR or data[:8] != b"A7800MS\0":
        raise MariaError("not a MARIA snapshot")
    reg = data[8:40]
    mem = data[40:40 + 0x10000]
    if len(mem) < 0x10000:
        raise MariaError("memory image is incomplete")
    dll = reg[0x10] << 8 | reg[0x0C]
    back = reg[0]
    frame, zones, objects, dli, bare = [], 0, 0, 0, 0
    while len(frame) < LINES:
        e = mem[dll:dll + 3]
        dll = (dll + 3) & 0xFFFF
        height = (e[0] & 15) + 1
        dli += e[0] >> 7
        dl = e[2] << 8 | e[1]
        zones += 1
        heads, p = [], dl
        while True:
            b1 = mem[(p + 1) & 0xFFFF]
            if b1 == 0:
                break
            if len(heads) >= 256:
                raise MariaError("display list does not end")
            h = [mem[(p + i) & 0xFFFF] for i in range(4)]
            heads.append((h[2] << 8 | h[0], h[1] >> 5, h[1] & 31, h[3]))
            p += 4
        objects += len(heads)
        for k in range(height):
            off = k
            line, hit = [back] * 256, [False] * 256
            for gfx, pal, width, hpos in heads:
                base = ((gfx >> 8) + off & 0xFF) << 8 | (gfx & 0xFF)
                for i in range(width):
                    b = mem[(base + i) & 0xFFFF]
                    for j in range(4):
                        v = b >> (2 * j) & 3
                        if v:
                            x = hpos + 4 * i + j
                            if x >= 256:
                                continue
                            line[x], hit[x] = reg[1 + 3 * pal + v - 1], True
            if len(frame) < LINES:
                bare += hit[:WIDTH].count(False)
            frame.append(["%02x" % c for c in line[:WIDTH]])
    frame = frame[:LINES]
    used = {c for row in frame for c in row}
    return {"zones": zones, "objects": objects, "dli_zones": dli, "colors_used": len(used),
            "background_pixels": bare,
            "first_row": _runs(frame[0]), "row_100": _runs(frame[100]), "last_row": _runs(frame[-1])}
