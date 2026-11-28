"""SNES Mode 7 snapshots (.m7): render the 256 x 224 BG1 frame and summarise it."""
import struct
import zlib

W, H = 256, 224


class M7Error(ValueError):
    pass


def _s(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def render(sel, a, b, c, d, cx, cy, hofs, vofs, vram):
    a, b, c, d = (_s(t, 16) for t in (a, b, c, d))
    cx, cy, hofs, vofs = (t & 0x1FFF for t in (cx, cy, hofs, vofs))
    over, vflip, hflip = sel >> 6 & 3, sel & 1, sel >> 1 & 1
    rd = lambda i: vram[i] if i < len(vram) else 0
    hc, vc = hofs - cx, vofs - cy
    out = bytearray(W * H)
    for line in range(H):
        y = 255 - line if vflip else line
        ox = a * hc + b * vc + b * y + (cx << 8)
        oy = c * hc + d * vc + d * y + (cy << 8)
        for sx in range(W):
            x = 255 - sx if hflip else sx
            px, py = (ox + a * x) >> 8, (oy + c * x) >> 8
            oob = bool((px | py) & ~0x3FF)
            tile = 0 if over == 2 and oob else rd((((py >> 3 & 0x7F) * 0x80 + (px >> 3 & 0x7F)) << 1) | 1)
            pal = 0 if over == 3 and oob else rd((tile << 6 | ((py & 7) << 3) + (px & 7)) << 1)
            out[line * W + sx] = pal
    return bytes(out)


def parse(data):
    if len(data) < 8 or data[:8] != b"SNESM7\0\0":
        raise M7Error("not a Mode 7 snapshot")
    if len(data) < 8 + 17 + 4:
        raise M7Error("register block is truncated")
    sel, a, b, c, d, cx, cy, h, v = struct.unpack_from("<B4H4H", data, 8)
    n = struct.unpack_from("<I", data, 25)[0]
    ram = data[29:29 + n]
    if len(ram) < n:
        raise M7Error("VRAM is truncated")
    img = render(sel, a, b, c, d, cx, cy, h, v, ram)
    hist = {}
    for p in img:
        if p:
            hist[p] = hist.get(p, 0) + 1
    at = lambda x, y: img[y * W + x]
    return {"screen_over": ("wrap", "wrap", "transparent", "tile0")[sel >> 6 & 3], "hflip": bool(sel & 2), "vflip": bool(sel & 1),
            "matrix": ["%.4f" % (_s(t, 16) / 256) for t in (a, b, c, d)], "center": [cx & 0x1FFF, cy & 0x1FFF],
            "scroll": [h & 0x1FFF, v & 0x1FFF], "opaque": sum(hist.values()),
            "histogram": {str(k): hist[k] for k in sorted(hist)},
            "samples": [at(0, 0), at(255, 0), at(0, 223), at(255, 223), at(128, 112), at(37, 191), at(200, 50)],
            "crc32": "%08x" % zlib.crc32(img)}
