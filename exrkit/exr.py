"""OpenEXR file headers: version flags, attributes, channel lists, windows, compression, parts."""
import struct
from fractions import Fraction

MAGIC = b"\x76\x2f\x31\x01"
PIXEL_TYPES = ("half", "uint", "float")
COMPRESSIONS = ("none", "rle", "zips", "zip", "piz", "pxr24", "b44", "b44a")
LINES = {"none": 1, "rle": 1, "zips": 1, "zip": 1, "pxr24": 1, "piz": 1, "b44": 1, "b44a": 1}
ORDERS = ("increasing_y", "decreasing_y", "random_y")
LEVELS = ("one_level", "mipmap", "ripmap")


class ExrError(ValueError):
    pass


def cstr(data, p):
    end = data.index(b"\0", p)
    return data[p:end].decode("latin-1"), end + 1


def box(v):
    x0, y0, x1, y1 = struct.unpack("<4i", v)
    return {"origin": [x0, y0], "size": [x1 - x0, y1 - y0]}


def channels(v):
    out, p = [], 0
    while v[p] != 0:
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
        if typ not in TYPES:
            raise ExrError("unknown attribute type %r" % typ)
        size, = struct.unpack_from("<i", data, p)
        p += 4
        attrs[name] = (typ, data[p:p + size])
        p += size
    return attrs, p + 1


def describe(attrs, tiled_file):
    get = lambda n: attrs.get(n, (None, None))[1]
    comp = COMPRESSIONS[get("compression")[0]] if get("compression") is not None and get("compression")[0] < len(COMPRESSIONS) else None
    dw = box(get("dataWindow")) if get("dataWindow") is not None else None
    part = {"name": get("name").split(b"\0")[0].decode("latin-1") if get("name") is not None else None,
            "type": get("type").split(b"\0")[0].decode("latin-1") if get("type") is not None else ("tiledimage" if tiled_file else "scanlineimage"),
            "channels": channels(get("channels")) if get("channels") is not None else [],
            "compression": comp, "data_window": dw,
            "display_window": box(get("displayWindow")) if get("displayWindow") is not None else None,
            "line_order": ORDERS[get("lineOrder")[0]] if get("lineOrder") is not None else None,
            "pixel_aspect": round(struct.unpack("<f", get("pixelAspectRatio"))[0], 6) if get("pixelAspectRatio") is not None else None,
            "tiles": None, "chunks": None, "frame_rate": None, "custom": []}
    return part


TYPES = {"chlist", "compression", "box2i", "lineOrder", "float", "v2f", "tiledesc", "string", "rational"}
KNOWN = {"channels", "compression", "dataWindow", "displayWindow", "lineOrder", "pixelAspectRatio",
         "screenWindowCenter", "screenWindowWidth", "tiles", "name", "type", "version", "chunkCount", "framesPerSecond"}


def parse(data):
    if data[:4] != MAGIC:
        raise ExrError("not an OpenEXR file")
    v, = struct.unpack_from("<I", data, 4)
    out = {"version": v & 0xFF, "tiled": bool(v & 0x100), "long_names": bool(v & 0x400), "deep": bool(v & 0x800),
           "multipart": bool(v & 0x1000), "parts": []}
    p = 8
    attrs, p = header(data, p)
    out["parts"].append(describe(attrs, out["tiled"]))
    return out
