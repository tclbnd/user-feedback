"""OpenEXR file headers: version flags, attributes, channel lists, windows, compression, parts."""
import struct
from fractions import Fraction

MAGIC = b"\x76\x2f\x31\x01"
PIXEL_TYPES = ("uint", "half", "float")
COMPRESSIONS = ("none", "rle", "zips", "zip", "piz", "pxr24", "b44", "b44a", "dwaa", "dwab")
LINES = {"none": 1, "rle": 1, "zips": 1, "zip": 16, "piz": 32, "pxr24": 16,
         "b44": 32, "b44a": 32, "dwaa": 32, "dwab": 256}
ORDERS = ("increasing_y", "decreasing_y", "random_y")
LEVELS = ("one_level", "mipmap", "ripmap")
ROUNDINGS = ("down", "up")


class ExrError(ValueError):
    pass


def cstr(data, p):
    end = data.index(b"\0", p)
    return data[p:end].decode("latin-1"), end + 1


def string(v):
    """A string attribute: a zero-terminated C string, sometimes with padding."""
    return v.split(b"\0", 1)[0].decode("latin-1")


def box(v):
    """A box2i attribute: the corners are inclusive, so the size is the span plus one."""
    x0, y0, x1, y1 = struct.unpack_from("<4i", v)
    return {"origin": [x0, y0], "size": [x1 - x0 + 1, y1 - y0 + 1]}


def tiledesc(v):
    """A tile description: tile size, level mode (low nibble) and rounding (high nibble)."""
    x_size, y_size, mode = struct.unpack_from("<IIB", v)
    level_mode, rounding = mode & 0x0F, (mode >> 4) & 0x0F
    return {"size": [x_size, y_size],
            "level_mode": LEVELS[level_mode] if level_mode < len(LEVELS) else "mode_%d" % level_mode,
            "rounding": ROUNDINGS[rounding] if rounding < len(ROUNDINGS) else "rounding_%d" % rounding}


def rational(v):
    num, den = struct.unpack_from("<ii", v)
    return str(Fraction(num, den))


def channels(v):
    out, p = [], 0
    while p < len(v) and v[p] != 0:
        name, p = cstr(v, p)
        ptype, linear, xs, ys = struct.unpack_from("<iB3xii", v, p)
        p += 16
        out.append({"name": name, "type": PIXEL_TYPES[ptype] if 0 <= ptype < 3 else "type_%d" % ptype,
                    "sampling": [xs, ys]})
    return out


def header(data, p):
    attrs = {}
    while data[p] != 0:
        name, p = cstr(data, p)
        typ, p = cstr(data, p)
        size, = struct.unpack_from("<i", data, p)
        p += 4
        if size < 0 or p + size > len(data):
            raise ExrError("attribute %r overruns the header" % name)
        attrs[name] = (typ, data[p:p + size])
        p += size
    return attrs, p + 1


def level_size(n, level, rounding):
    """One side of resolution level `level`, as OpenEXR's levelSize()."""
    scale = 1 << level
    size = n // scale
    if rounding == "up" and size * scale < n:
        size += 1
    return max(size, 1)


def level_count(n, rounding):
    """The number of resolution levels of a side of length n, as roundLog2() + 1."""
    return (n.bit_length() - 1 if rounding == "down" else (n - 1).bit_length()) + 1


def levels(size, mode, rounding):
    """The (x, y) sizes of every resolution level of a tile description."""
    w, h = size
    if mode == "one_level":
        return [(max(w, 1), max(h, 1))]
    if mode == "mipmap":
        n = level_count(max(w, h), rounding)
        return [(level_size(w, l, rounding), level_size(h, l, rounding)) for l in range(n)]
    return [(level_size(w, x, rounding), level_size(h, y, rounding))
            for y in range(level_count(h, rounding))
            for x in range(level_count(w, rounding))]


def chunks(part):
    """The chunks the part needs: scan-line blocks, or the tiles of all its levels."""
    window, tiles = part["data_window"], part["tiles"]
    if window is None:
        return None
    w, h = window["size"]
    if tiles is None:
        lines = LINES.get(part["compression"])
        return None if lines is None else (max(h, 0) + lines - 1) // lines
    tw, th = tiles["size"]
    if tw <= 0 or th <= 0:
        return None
    return sum(((lw + tw - 1) // tw) * ((lh + th - 1) // th)
               for lw, lh in levels([w, h], tiles["level_mode"], tiles["rounding"]))


# The attributes this module describes and the type each one is stored with;
# anything else in a header is reported by name in the part's `custom` list.
FIELDS = {"name": "string", "type": "string", "channels": "chlist", "compression": "compression",
          "dataWindow": "box2i", "displayWindow": "box2i", "lineOrder": "lineOrder",
          "pixelAspectRatio": "float", "tiles": "tiledesc", "framesPerSecond": "rational"}
# The rest of the attributes of the file layout, which the summary leaves out but
# which are not attributes an application made up either.
KNOWN = set(FIELDS) | {"screenWindowCenter", "screenWindowWidth", "version", "chunkCount", "maxSamplesPerPixel"}


def describe(attrs, tiled_file):
    values = {}
    for name, typ in FIELDS.items():
        value = attrs.get(name)
        # Attributes of any type are kept, so only read the ones whose type is
        # the one this field expects.
        values[name] = value[1] if value is not None and value[0] == typ else None

    compression = values["compression"]
    tile_description = values["tiles"]
    part = {"name": string(values["name"]) if values["name"] is not None else None,
            "type": string(values["type"]) if values["type"] is not None else ("tiledimage" if tiled_file else "scanlineimage"),
            "channels": channels(values["channels"]) if values["channels"] is not None else [],
            "compression": COMPRESSIONS[compression[0]] if compression is not None and compression[0] < len(COMPRESSIONS) else None,
            "data_window": box(values["dataWindow"]) if values["dataWindow"] is not None else None,
            "display_window": box(values["displayWindow"]) if values["displayWindow"] is not None else None,
            "line_order": ORDERS[values["lineOrder"][0]] if values["lineOrder"] is not None and values["lineOrder"][0] < len(ORDERS) else None,
            "pixel_aspect": round(struct.unpack_from("<f", values["pixelAspectRatio"])[0], 6) if values["pixelAspectRatio"] is not None else None,
            "tiles": tiledesc(tile_description) if tile_description is not None else None,
            "chunks": None,
            "frame_rate": rational(values["framesPerSecond"]) if values["framesPerSecond"] is not None else None,
            "custom": sorted(n for n in attrs if n not in KNOWN)}
    part["chunks"] = chunks(part)
    return part


def parse(data):
    if data[:4] != MAGIC:
        raise ExrError("not an OpenEXR file")
    v, = struct.unpack_from("<I", data, 4)
    out = {"version": v & 0xFF, "tiled": bool(v & 0x200), "long_names": bool(v & 0x400), "deep": bool(v & 0x800),
           "multipart": bool(v & 0x1000), "parts": []}
    # Only a single-part file's type comes from the version flag; each part of
    # a multipart file carries its own type attribute instead.
    tiled_file = out["tiled"] and not out["multipart"]
    p = 8
    if out["multipart"]:
        # Every part header ends with an empty name; an empty header ends the list.
        while True:
            attrs, p = header(data, p)
            if not attrs:
                break
            out["parts"].append(describe(attrs, tiled_file))
        if not out["parts"]:
            raise ExrError("no part headers")
    else:
        attrs, p = header(data, p)
        out["parts"].append(describe(attrs, tiled_file))
    return out
