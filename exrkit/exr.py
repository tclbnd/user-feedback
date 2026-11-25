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
ROUNDING = ("down", "up")


class ExrError(ValueError):
    pass


def cstr(data, p):
    end = data.index(b"\0", p)
    return data[p:end].decode("latin-1"), end + 1


def box(v):
    x0, y0, x1, y1 = struct.unpack("<4i", v)
    return {"origin": [x0, y0], "size": [x1 - x0 + 1, y1 - y0 + 1]}


def channels(v):
    out, p = [], 0
    while v[p] != 0:
        name, p = cstr(v, p)
        ptype, linear, xs, ys = struct.unpack_from("<iB3xii", v, p)
        p += 16
        out.append({"name": name, "type": PIXEL_TYPES[ptype] if 0 <= ptype < 3 else "type_%d" % ptype,
                    "sampling": [xs, ys]})
    return out


def tiledesc(v):
    tx, ty, mode = struct.unpack("<IIB", v)
    level, rounding = mode & 0x0F, (mode >> 4) & 0x0F
    return {"size": [tx, ty],
            "level_mode": LEVELS[level] if level < len(LEVELS) else "mode_%d" % level,
            "rounding": ROUNDING[rounding] if rounding < len(ROUNDING) else "rounding_%d" % rounding}


def frame_rate(v):
    n, d = struct.unpack("<2i", v)
    f = Fraction(n, d)
    return "%d/%d" % (f.numerator, f.denominator)


def level_size(n, level, up):
    for _ in range(level):
        n = -(-n // 2) if up else n // 2
    return max(n, 1)


def level_count(n, up):
    count = 1
    while n > 1:
        n = -(-n // 2) if up else n // 2
        count += 1
    return count


def chunks(comp, dw, td):
    if dw is None:
        return None
    w, h = dw["size"]
    if td is None:
        if comp not in LINES:
            return None
        return -(-h // LINES[comp])
    tx, ty = td["size"]
    up = td["rounding"] == "up"
    mode = td["level_mode"]
    if mode == "one_level":
        pairs = [(0, 0)]
    elif mode == "mipmap":
        pairs = [(l, l) for l in range(level_count(max(w, h), up))]
    elif mode == "ripmap":
        pairs = [(lx, ly) for lx in range(level_count(w, up)) for ly in range(level_count(h, up))]
    else:
        return None
    total = 0
    for lx, ly in pairs:
        total += (-(-level_size(w, lx, up) // tx)) * (-(-level_size(h, ly, up) // ty))
    return total


def header(data, p):
    attrs = {}
    while data[p] != 0:
        name, p = cstr(data, p)
        typ, p = cstr(data, p)
        size, = struct.unpack_from("<i", data, p)
        p += 4
        attrs[name] = (typ, data[p:p + size])
        p += size
    return attrs, p + 1


def describe(attrs, tiled_file):
    get = lambda n: attrs.get(n, (None, None))[1]
    comp_v = get("compression")
    comp = COMPRESSIONS[comp_v[0]] if comp_v is not None and comp_v[0] < len(COMPRESSIONS) else None
    dw = box(get("dataWindow")) if get("dataWindow") is not None else None
    td = tiledesc(get("tiles")) if get("tiles") is not None else None
    fps = get("framesPerSecond")
    part = {"name": get("name").split(b"\0")[0].decode("latin-1") if get("name") is not None else None,
            "type": get("type").split(b"\0")[0].decode("latin-1") if get("type") is not None else ("tiledimage" if tiled_file else "scanlineimage"),
            "channels": channels(get("channels")) if get("channels") is not None else [],
            "compression": comp, "data_window": dw,
            "display_window": box(get("displayWindow")) if get("displayWindow") is not None else None,
            "line_order": ORDERS[get("lineOrder")[0]] if get("lineOrder") is not None else None,
            "pixel_aspect": round(struct.unpack("<f", get("pixelAspectRatio"))[0], 6) if get("pixelAspectRatio") is not None else None,
            "tiles": td, "chunks": chunks(comp, dw, td),
            "frame_rate": frame_rate(fps) if fps is not None else None,
            "custom": sorted(n for n in attrs if n not in KNOWN)}
    return part


KNOWN = {"channels", "compression", "dataWindow", "displayWindow", "lineOrder", "pixelAspectRatio",
         "screenWindowCenter", "screenWindowWidth", "tiles", "name", "type", "version", "chunkCount", "framesPerSecond"}


def parse(data):
    if data[:4] != MAGIC:
        raise ExrError("not an OpenEXR file")
    v, = struct.unpack_from("<I", data, 4)
    out = {"version": v & 0xFF, "tiled": bool(v & 0x200), "long_names": bool(v & 0x400), "deep": bool(v & 0x800),
           "multipart": bool(v & 0x1000), "parts": []}
    p = 8
    while True:
        attrs, p = header(data, p)
        out["parts"].append(describe(attrs, out["tiled"]))
        if not out["multipart"] or data[p] == 0:
            break
    return out
