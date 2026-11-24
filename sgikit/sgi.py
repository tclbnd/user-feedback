"""SGI image files: header, verbatim and RLE planar storage, offset tables, 8- and 16-bit channels."""
import struct

CMAPS = ("normal", "dithered", "screen", "colormap")


class SgiError(ValueError):
    pass


def decode_rle(data, start, w, bpc):
    """One RLE row."""
    get = (lambda p: data[p]) if bpc == 1 else (lambda p: struct.unpack_from(">H", data, p)[0])
    out, p = [], start
    while len(out) < w:
        if p + bpc > len(data):
            return None
        v = get(p)
        p += bpc
        n = v & 0x7F
        if n == 0:
            break
        if not v & 0x80:
            if p + n * bpc > len(data):
                return None
            out += [get(p + i * bpc) for i in range(n)]
            p += n * bpc
        else:
            if p + bpc > len(data):
                return None
            out += [get(p)] * n
            p += bpc
    return out[:w] if len(out) >= w else None


def parse(data):
    if len(data) < 512 or struct.unpack_from(">h", data, 0)[0] != 474:
        raise SgiError("not an SGI image")
    storage, bpc, dim, w, h, z = struct.unpack_from(">BBHHHH", data, 2)
    pixmin, pixmax = struct.unpack_from(">ii", data, 12)
    cmap, = struct.unpack_from(">i", data, 104)
    if bpc != 1 or storage not in (0, 1):
        raise SgiError("unsupported storage %d / %d bytes per channel" % (storage, bpc))
    h = 1 if dim == 1 else h                                # dimension 1 is a single row, 2 a single channel
    z = 1 if dim in (1, 2) else z
    rows = {}
    if storage == 0:
        for c in range(z):
            for y in range(h):
                p = 512 + y * w * z + c
                rows[(y, c)] = list(data[p:p + w * z:z]) if p + (w - 1) * z < len(data) else None
    else:
        n = h * z
        starts = struct.unpack_from(">%dI" % n, data, 512)
        for c in range(z):
            for y in range(h):
                rows[(y, c)] = decode_rle(data, starts[y * z + c], w, bpc)
    complete = all(r is not None for r in rows.values())

    def point(x, top):
        y = top
        vals = [rows[(y, c)][x] if rows[(y, c)] else None for c in range(z)]
        return None if None in vals else vals

    return {"storage": ("verbatim", "rle")[storage], "bytes_per_channel": bpc, "dimension": dim,
            "width": w, "height": h, "channels": z, "name": data[24:104].split(b"\0")[0].decode("latin-1") or None,
            "pixel_range": [pixmin, pixmax], "colormap": CMAPS[cmap] if 0 <= cmap < 4 else "type_%d" % cmap,
            "complete": complete,
            "points": {"top_left": point(0, 0), "top_right": point(w - 1, 0), "bottom_left": point(0, h - 1),
                       "center": point(w // 2, h // 2)},
            "max_value": max((v for r in rows.values() if r for v in r), default=None)}
