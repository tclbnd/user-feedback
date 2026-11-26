"""RISC OS sprite files (.spr): sprite area header, sprites, palettes, masks."""
import struct

MODE_BPP = {9: 4, 12: 4, 13: 8, 15: 8}


class AreaError(ValueError):
    pass


def parse(data):
    if len(data) < 12:
        raise AreaError("not a sprite file")
    count, first, free = struct.unpack_from("<3I", data)
    if first < 16 or count > 4096:
        raise AreaError("not a sprite file")
    out, p, complete = [], first, True
    for _ in range(count):
        if p + 44 > len(data):
            complete = False
            break
        nxt = struct.unpack_from("<I", data, p)[0]
        name = data[p + 4:p + 16].split(b"\0")[0].decode("latin-1").rstrip()
        wwords, hm1, lbit, rbit, img, msk, mode = struct.unpack_from("<7I", data, p + 16)
        bpp = MODE_BPP.get(mode)
        if bpp is None:
            raise AreaError("unsupported mode %#x" % mode)
        width = (wwords + 1) * 32 // bpp
        height = hm1 + 1
        stride = (wwords + 1) * 4
        pal = [struct.unpack_from("<I", data, p + o)[0] for o in range(44, img, 8)]
        pal = ["#%06x" % (c & 0xFFFFFF) for c in pal]
        if p + img + stride * height > len(data):
            complete = False
            out.append({"name": name, "width": width, "height": height, "bpp": bpp, "has_mask": False,
                        "palette_entries": len(pal), "first_row": None, "last_pixel": None, "transparent_pixels": None})
            p += nxt
            continue

        def colour(x, y):
            bit = x * bpp
            byte = data[p + img + y * stride + bit // 8]
            v = byte >> (8 - bpp - bit % 8) & ((1 << bpp) - 1)
            return pal[v] if v < len(pal) else None

        out.append({"name": name, "width": width, "height": height, "bpp": bpp, "has_mask": False,
                    "palette_entries": len(pal), "first_row": [colour(x, 0) for x in range(min(8, width))],
                    "last_pixel": colour(width - 1, height - 1), "transparent_pixels": 0})
        p += nxt
    return {"sprites_declared": count, "complete": complete, "sprites": out}
