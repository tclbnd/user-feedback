"""Dreamcast PVR textures (.pvr): GBIX and PVRT chunks, twiddled, VQ and rectangle data."""
import struct

PIX = {0: "ARGB1555", 1: "RGB565", 2: "ARGB4444"}
DATA = {1: "twiddled", 3: "vq", 9: "rectangle"}


class PvrError(ValueError):
    pass


def _runs(line):
    out = []
    for c in line:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


def _tw(x, y):
    v = 0
    for b in range(11):
        v |= (y >> b & 1) << (2 * b) | (x >> b & 1) << (2 * b + 1)
    return v


def _e(v, bits):
    return (v << (8 - bits)) | (v >> (2 * bits - 8))


def colour(pix, v):
    if pix == 0:
        return (_e(v >> 10 & 31, 5), _e(v >> 5 & 31, 5), _e(v & 31, 5), 255 if v >> 15 else 0)
    if pix == 1:
        return (_e(v >> 11 & 31, 5), _e(v >> 5 & 63, 6), _e(v & 31, 5), 255)
    return ((v >> 8 & 15) * 17, (v >> 4 & 15) * 17, (v & 15) * 17, (v >> 12) * 17)


def parse(data):
    pos, gbix = 0, None
    if data[:4] == b"GBIX":
        if len(data) < 12:
            raise PvrError("truncated GBIX chunk")
        gbix_size = struct.unpack_from("<I", data, 4)[0]
        if gbix_size < 4 or len(data) < 8 + gbix_size:
            raise PvrError("bad GBIX chunk")
        gbix = struct.unpack_from("<I", data, 8)[0]
        pos = 8 + gbix_size
    if data[pos:pos + 4] != b"PVRT" or len(data) < pos + 16:
        raise PvrError("not a PVR texture")
    pix, fmt, _, w, h = struct.unpack_from("<BBHHH", data, pos + 8)
    if pix not in PIX or fmt not in DATA:
        raise PvrError("unsupported PVR format %d/%d" % (pix, fmt))
    body = data[pos + 16:]
    need = {1: 2 * w * h, 9: 2 * w * h, 3: 2048 + (w // 2) * (h // 2)}[fmt]
    out = {"pixel_format": PIX[pix], "data_format": DATA[fmt], "width": w, "height": h, "global_index": gbix,
           "complete": len(body) >= need}
    if not out["complete"] or w == 0 or h == 0:
        return {**out, "vq_codes": None, "colors_used": None, "transparent_pixels": None, "first_row": None, "last_row": None}
    if fmt in (1, 3) and w != h:
        raise PvrError("%s textures must be square" % DATA[fmt])
    img = [[None] * w for _ in range(h)]
    codes = None
    words = struct.unpack_from("<%dH" % (w * h), body) if fmt in (1, 9) else None
    if fmt == 9:
        for y in range(h):
            row = y * w
            for x in range(w):
                img[y][x] = colour(pix, words[row + x])
    elif fmt == 1:
        for y in range(h):
            for x in range(w):
                img[y][x] = colour(pix, words[_tw(x, y)])
    else:
        book = struct.unpack_from("<1024H", body)
        idx = body[2048:]
        codes = set()
        for y in range(h):
            by = y // 2
            sub_y = y & 1
            for x in range(w):
                bx = x // 2
                e = idx[_tw(bx, by)]
                codes.add(e)
                sub = (x & 1) | (sub_y << 1)
                img[y][x] = colour(pix, book[4 * e + sub])
        codes = len(codes)
    hexes = [["#%02x%02x%02x%02x" % c for c in row] for row in img]
    return {**out, "vq_codes": codes, "colors_used": len({c for row in hexes for c in row}),
            "transparent_pixels": sum(c[3] == 0 for row in img for c in row),
            "first_row": _runs(hexes[0]), "last_row": _runs(hexes[-1])}
