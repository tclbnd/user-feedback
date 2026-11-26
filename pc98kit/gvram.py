"""PC-98 graphics VRAM dumps (.98g): display mode, GDC scroll areas, analogue palette and the four planes."""
import struct

W, H, PLANE, HDR = 640, 400, 0x8000, 8 + 16 + 48

# Digital RGB has one bit per gun.  Colour code bits follow the plane order:
# A8000h = blue, B0000h = red, B8000h = green.  The fourth plane is not part
# of 8-colour digital output, so codes 8-15 repeat codes 0-7.
_DIGITAL_RGB = (
    (0, 0, 0), (0, 0, 15), (15, 0, 0), (15, 0, 15),
    (0, 15, 0), (0, 15, 15), (15, 15, 0), (15, 15, 15),
)
DIGITAL_PALETTE = [
    "#%02x%02x%02x" % (r * 17, g * 17, b * 17)
    for _ in range(2)
    for r, g, b in _DIGITAL_RGB
]


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
    if analog:
        pal = []
        for i in range(16):
            # Port order is AAh=blue, ACh=red, AEh=green.
            blue, red, green = raw[3 * i:3 * i + 3]
            red &= 15
            green &= 15
            blue &= 15
            pal.append("#%02x%02x%02x" % (red * 17, green * 17, blue * 17))
    else:
        pal = list(DIGITAL_PALETTE)
    pl = [data[HDR + k * PLANE:HDR + (k + 1) * PLANE] for k in range(4)]
    out = {"mode": "16-colour" if analog else "8-colour", "palette": pal, "complete": len(pl[3]) == PLANE}
    if not out["complete"]:
        return {**out, "colors_used": None, "code_pixels": None, "first_row": None, "middle_row": None, "last_row": None}
    blue, red, green, extra = pl
    seen, cnt, rows = set(), [0] * 16, {}
    for y in range(H):
        if y < len1:
            # GDC start addresses are word addresses; each word is two bytes
            # in a graphics plane.
            start = sad1 * 2 + y * 80
        else:
            start = sad2 * 2 + (y - len1) * 80
        line = []
        for xb in range(80):
            o = (start + xb) % PLANE
            vb, vr, vg, ve = blue[o], red[o], green[o], extra[o]
            for k in range(8):
                s = 7 - k
                c = (vb >> s & 1) | (vr >> s & 1) << 1 | (vg >> s & 1) << 2 | (ve >> s & 1) << 3
                cnt[c] += 1
                line.append(pal[c])
        seen.update(line)
        if y in (0, 200, H - 1):
            rows[y] = _runs(line)
    return {**out, "colors_used": len(seen), "code_pixels": cnt, "first_row": rows[0], "middle_row": rows[200], "last_row": rows[H - 1]}
