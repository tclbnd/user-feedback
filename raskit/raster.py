"""Sun raster images (.ras): header, colour map and image data."""
import struct

MAGIC = 0x59A66A95


class RasterError(ValueError):
    pass


def _unpack(d, need):
    out, i = bytearray(), 0
    while i < len(d) and len(out) < need:
        b = d[i]
        if b == 0x80 and i + 2 < len(d):
            out += bytes([d[i + 2]]) * d[i + 1]
            i += 3
        else:
            out.append(b)
            i += 1
    return bytes(out[:need])


def parse(data):
    if len(data) < 32:
        raise RasterError("not a Sun raster image")
    magic, w, h, depth, length, typ, maptype, maplen = struct.unpack_from(">8I", data)
    if magic != MAGIC or w == 0 or h == 0:
        raise RasterError("not a Sun raster image")
    if depth not in (1, 8, 24):
        raise RasterError("unsupported depth %d" % depth)
    m = data[32:32 + maplen]
    cmap = None
    if maptype == 1 and maplen:
        cmap = [tuple(m[i:i + 3]) for i in range(0, maplen - 2, 3)]
    body = data[32 + maplen:]
    row = (w * depth + 7) // 8
    if typ == 2:
        body = _unpack(body, row * h)
    rows = min(h, len(body) // row)
    seen = set()
    first = []
    last = None
    for y in range(rows):
        line = body[y * row:(y + 1) * row]
        for x in range(w):
            if depth == 1:
                v = line[x >> 3] >> (7 - (x & 7)) & 1
                c = cmap[v] if cmap else ((0, 0, 0) if v else (255, 255, 255))
            elif depth == 8:
                v = line[x]
                c = (cmap[v] if v < len(cmap) else (0, 0, 0)) if cmap else (v, v, v)
            else:
                p = line[x * 3:x * 3 + 3]
                c = (p[0], p[1], p[2])
            hx = "#%02x%02x%02x" % c
            seen.add(hx)
            if y == 0 and x < 8:
                first.append(hx)
            last = hx
    return {"width": w, "height": h, "depth": depth, "type": typ, "colormap_entries": len(cmap) if cmap else 0,
            "row_bytes": row, "rows": rows, "complete": rows == h, "colors_used": len(seen),
            "first_row": first, "last_pixel": last}
