"""PC-98 graphics VRAM dumps (.98g): display mode, GDC scroll areas, analogue palette and the four planes."""
import struct

W, H, PLANE, HDR = 640, 400, 0x8000, 8 + 16 + 48


class GvError(ValueError):
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
    if len(data) < HDR or data[:8] != b"PC98GV\0\0":
        raise GvError("not a PC-98 graphics VRAM dump")
    analog, sad1, len1, sad2 = struct.unpack_from("<BxxxIHIxx", data, 8)
    analog &= 1
    raw = data[24:72]
    pal = ["#%02x%02x%02x" % (raw[3 * i] * 17, raw[3 * i + 1] * 17, raw[3 * i + 2] * 17) for i in range(16)]
    pl = [data[HDR + k * PLANE:HDR + (k + 1) * PLANE] for k in range(4)]
    out = {"mode": "16-colour" if analog else "8-colour", "palette": pal, "complete": len(pl[3]) == PLANE}
    if not out["complete"]:
        return {**out, "colors_used": None, "code_pixels": None, "first_row": None, "middle_row": None, "last_row": None}
    r, g, b, e = pl
    seen, cnt, rows = set(), [0] * 16, {}
    for y in range(H):
        start = sad1 + y * 80
        line = []
        for xb in range(80):
            o = start + xb
            vr, vg, vb, ve = r[o], g[o], b[o], e[o]
            for k in range(8):
                s = 7 - k
                c = (vr >> s & 1) | (vg >> s & 1) << 1 | (vb >> s & 1) << 2 | (ve >> s & 1) << 3
                cnt[c] += 1
                line.append(pal[c])
        seen.update(line)
        if y in (0, 200, H - 1):
            rows[y] = _runs(line)
    return {**out, "colors_used": len(seen), "code_pixels": cnt, "first_row": rows[0], "middle_row": rows[200], "last_row": rows[H - 1]}
