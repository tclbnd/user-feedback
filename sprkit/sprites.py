"""C64 sprite snapshots (.vsp): VIC-II registers $D000-$D02E, sprite pointers, 16 KB VIC bank."""

W, H = 320, 200


class SpriteError(ValueError):
    pass


def parse(data):
    if len(data) < 6 + 47 + 8 or data[:6] != b"C64SPR":
        raise SpriteError("not a sprite snapshot")
    r = data[6:53]
    ptrs = data[53:61]
    mem = data[61:61 + 16384]
    complete = len(mem) == 16384
    bg = r[0x21]
    canvas = [[bg & 15] * W for _ in range(H)]
    info = []
    for n in range(8):
        if not r[0x15] >> n & 1:
            continue
        x = r[2 * n]
        y = r[2 * n + 1]
        col = r[0x27 + n] & 15
        base = ptrs[n] * 64
        blk = mem[base:base + 63]
        if len(blk) < 63:
            info.append({"n": n, "x": x, "y": y, "width": 24, "height": 21, "pixels": None})
            continue
        pix = 0
        for row in range(21):
            bits = blk[3 * row] << 16 | blk[3 * row + 1] << 8 | blk[3 * row + 2]
            for i in range(24):
                if bits >> (23 - i) & 1:
                    pix += 1
                    sx, sy = x + i, y + row
                    if 0 <= sx < W and 0 <= sy < H:
                        canvas[sy][sx] = col
        info.append({"n": n, "x": x, "y": y, "width": 24, "height": 21, "pixels": pix})
    hist = [0] * 16
    for row in canvas:
        for c in row:
            hist[c] += 1
    return {"complete": complete, "background": bg & 15, "sprites": info, "histogram": hist,
            "colors_used": sum(1 for h in hist if h)}
