"""NDS 3D texture captures (.ntx): TEXIMAGE_PARAM formats, palettes and 4x4 compressed blocks."""
import struct

NAMES = {1: "A3I5", 2: "4-colour", 3: "16-colour", 4: "256-colour", 5: "4x4", 6: "A5I3", 7: "direct"}
BITS = {1: 8, 2: 2, 3: 4, 4: 8, 6: 8}
HDR = 8 + 20


class TexError(ValueError):
    pass


def _runs(line):
    out = []
    for c in line:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


def _w5(v):
    return v << 3 | v >> 2


def parse(data):
    if len(data) < HDR or data[:8] != b"NDSTEX\0\0":
        raise TexError("not an NDS texture capture")
    prm, pbase, nt, np_, ni = struct.unpack_from("<IHxxIII", data, 8)
    fmt = prm >> 26 & 7
    if fmt not in NAMES:
        raise TexError("no texture format")
    w, h, c0 = 8 << (prm >> 20 & 7), 8 << (prm >> 23 & 7), prm >> 29 & 1
    tex = data[HDR:HDR + nt]
    pal = data[HDR + nt:HDR + nt + np_]
    idx = data[HDR + nt + np_:HDR + nt + np_ + ni]
    need = {5: w * h // 4, 7: w * h * 2}.get(fmt, w * h * BITS.get(fmt, 8) // 8)
    out = {"format": NAMES[fmt], "width": w, "height": h, "colour0_transparent": bool(c0),
           "complete": len(tex) >= need and len(data) >= HDR + nt + np_ + ni and (fmt != 5 or len(idx) >= w * h // 8)}
    if not out["complete"]:
        return {**out, "colors_used": None, "transparent_pixels": None, "first_row": None, "last_row": None}
    base = pbase * 16

    def col(addr):
        v = struct.unpack_from("<H", pal, addr)[0] if 0 <= addr and addr + 2 <= len(pal) else 0
        return (v & 31, v >> 5 & 31, v >> 10 & 31)

    img = [[None] * w for _ in range(h)]
    if fmt == 7:
        for y in range(h):
            for x in range(w):
                v = struct.unpack_from("<H", tex, 2 * (y * w + x))[0]
                img[y][x] = (v & 31, v >> 5 & 31, v >> 10 & 31, 31 if v >> 15 else 0)
    elif fmt == 5:
        for by in range(h // 4):
            for bx in range(w // 4):
                k = by * (w // 4) + bx
                t = struct.unpack_from("<I", tex, 4 * k)[0]
                pv = struct.unpack_from("<H", idx, 2 * k)[0]
                a, mode = base + (pv & 0x3FFF) * 4, pv >> 14
                c = [col(a), col(a + 2), col(a + 4), col(a + 6)]
                for i in range(16):
                    n = t >> (2 * i) & 3
                    clear = n == 3 and mode == 0
                    img[by * 4 + i // 4][bx * 4 + i % 4] = (0, 0, 0, 0) if clear else c[n] + (31,)
    else:
        bits = BITS[fmt]
        for y in range(h):
            for x in range(w):
                p = (y * w + x) * bits
                v = tex[p // 8] >> (8 - bits - p % 8) & ((1 << bits) - 1)
                if fmt == 1:
                    i, al = v >> 3, (v & 7) * 4
                elif fmt == 6:
                    i, al = v >> 5, v & 31
                else:
                    i, al = v, 31
                img[y][x] = (0, 0, 0, 0) if al == 0 else col(base + 2 * i) + (al,)
    hexes = [["#%02x%02x%02x%02x" % tuple(_w5(c) for c in p) for p in row] for row in img]
    return {**out, "colors_used": len({c for row in hexes for c in row}),
            "transparent_pixels": sum(p[3] == 0 for row in img for p in row),
            "first_row": _runs(hexes[0]), "last_row": _runs(hexes[-1])}
