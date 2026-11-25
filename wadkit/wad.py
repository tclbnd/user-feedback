"""Quake texture packages (.wad): directory, textures, pictures, palettes."""
import struct

TYPES = {0x40: "palette", 0x42: "qpic", 0x44: "miptex"}


class WadError(ValueError):
    pass


def _hex(pal, i):
    if pal is None or i >= len(pal):
        return None
    return "#%02x%02x%02x" % pal[i]


def parse(data):
    if len(data) < 12 or data[:4] != b"WAD2":
        raise WadError("not a WAD2 file")
    n, dirofs = struct.unpack_from("<2i", data, 4)
    if n < 0 or dirofs < 12:
        raise WadError("bad directory")
    entries, complete = [], True
    for i in range(n):
        e = dirofs + 32 * i
        if e + 32 > len(data):
            complete = False
            break
        pos, disk, size, typ, comp = struct.unpack_from("<3iBB", data, e)
        name = data[e + 16:e + 32].split(b"\0")[0].decode("latin-1")
        entries.append((name, pos, disk, typ))
    shared = None
    for name, pos, disk, typ in entries:
        if typ == 0x40 and pos + 768 <= len(data):
            shared = [tuple(data[pos + 3 * i:pos + 3 * i + 3]) for i in range(256)]
    out = []
    for name, pos, disk, typ in entries:
        kind = TYPES.get(typ, "other")
        end = min(len(data), pos + disk)
        if pos + disk > len(data):
            complete = False
        item = {"name": name, "type": kind, "width": None, "height": None, "mip_sizes": None, "first_pixel": None,
                "smallest_pixel": None, "transparent": False, "transparent_pixels": 0, "animation": None}
        if kind == "miptex" and pos + 40 <= end:
            w, h, *offs = struct.unpack_from("<6I", data, pos + 16)
            sizes = [w * h] * 4
            item.update(width=w, height=h, mip_sizes=sizes)
            if offs[0] + w * h <= len(data) and offs[3] + sizes[3] <= len(data):
                item["first_pixel"] = _hex(shared, data[offs[0]])
                item["smallest_pixel"] = _hex(shared, data[offs[3] + sizes[3] - 1])
        elif kind == "qpic" and pos + 8 <= end:
            w, h = struct.unpack_from("<2I", data, pos)
            item.update(width=w, height=h)
            if w and h and pos + 8 + w * h <= end:
                item["first_pixel"] = _hex(shared, data[pos + 8])
        out.append(item)
    return {"version": "WAD2", "lump_count": n, "complete": complete, "lumps": out}
