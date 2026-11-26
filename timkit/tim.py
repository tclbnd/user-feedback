"""PlayStation TIM textures (.tim): CLUT block, image block, texel colours."""
import struct


class TimError(ValueError):
    pass


def _w(v):
    return round(v * 255 / 31)


def _rgb(word):
    return "#%02x%02x%02x" % (_w(word >> 10 & 31), _w(word >> 5 & 31), _w(word & 31))


def parse(data):
    if len(data) < 8 or struct.unpack_from("<I", data)[0] != 0x10:
        raise TimError("not a TIM file")
    flags = struct.unpack_from("<I", data, 4)[0]
    pmode = flags & 7
    if pmode not in (2, 3):
        raise TimError("unsupported pixel mode %d" % pmode)
    bpp = 16 if pmode == 2 else 24
    p = 8
    clut = None
    if p + 12 > len(data):
        raise TimError("truncated image block")
    bnum, ix, iy, wu, h = struct.unpack_from("<I4H", data, p)
    width = wu
    stride = wu * 2
    body = data[p + 12:p + 12 + bnum]
    rows = min(h, len(body) // stride) if stride else 0
    seen, first, last, transparent, semi = set(), [], None, 0, 0
    for yy in range(rows):
        line = body[yy * stride:(yy + 1) * stride]
        for xx in range(width):
            if bpp == 24:
                r, g, b = line[3 * xx:3 * xx + 3] if 3 * xx + 3 <= len(line) else (0, 0, 0)
                colour = "#%02x%02x%02x" % (b, g, r)
            else:
                word = struct.unpack_from("<H", line, 2 * xx)[0]
                colour = _rgb(word)
                if word & 0x8000:
                    transparent += 1
            seen.add(colour)
            if yy == 0 and xx < 8:
                first.append(colour)
            last = colour
    return {"bpp": bpp, "clut": clut, "x": ix, "y": iy, "width": width, "height": h, "rows": rows,
            "complete": rows == h, "colors_used": len(seen), "first_row": first, "last_pixel": last,
            "transparent_pixels": transparent, "semi_transparent_pixels": semi}
