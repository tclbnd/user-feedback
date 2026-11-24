"""OpenEXR file headers: version flags, attributes, channel lists, windows, compression, parts."""
import struct
from fractions import Fraction

MAGIC = b"\x76\x2f\x31\x01"

VERSION_NUMBER_FIELD = 0x000000FF
TILED_FLAG = 0x00000200
LONG_NAMES_FLAG = 0x00000400
NON_IMAGE_FLAG = 0x00000800
MULTIPART_FLAG = 0x00001000

PIXEL_TYPES = ("uint", "half", "float")
COMPRESSIONS = ("none", "rle", "zips", "zip", "piz", "pxr24", "b44", "b44a", "dwaa", "dwab")
LINES = {"none": 1, "rle": 1, "zips": 1, "zip": 16, "piz": 32, "pxr24": 16,
         "b44": 32, "b44a": 32, "dwaa": 32, "dwab": 256}
ORDERS = ("increasing_y", "decreasing_y", "random_y")
LEVELS = ("one_level", "mipmap", "ripmap")

KNOWN = {"channels", "compression", "dataWindow", "displayWindow", "lineOrder", "pixelAspectRatio",
         "screenWindowCenter", "screenWindowWidth", "tiles", "name", "type", "version", "chunkCount", "framesPerSecond"}


class ExrError(ValueError):
    pass


def _unpack(fmt, data, offset=0):
    try:
        return struct.unpack_from(fmt, data, offset)
    except struct.error:
        return None


def cstr(data, p):
    end = data.find(b"\0", p)
    if end < 0:
        raise ExrError("unterminated name")
    return data[p:end].decode("latin-1"), end + 1


def string(value):
    return value.split(b"\0")[0].decode("latin-1")


def box(value):
    fields = _unpack("<4i", value)
    if fields is None:
        return None
    x0, y0, x1, y1 = fields
    return {"origin": [x0, y0], "size": [x1 - x0 + 1, y1 - y0 + 1]}


def channels(value):
    out, p = [], 0
    while p < len(value) and value[p] != 0:
        name, p = cstr(value, p)
        fields = _unpack("<iB3xii", value, p)
        if fields is None:
            raise ExrError("truncated channel %r" % name)
        ptype, _linear, xs, ys = fields
        p += 16
        out.append({"name": name,
                    "type": PIXEL_TYPES[ptype] if 0 <= ptype < len(PIXEL_TYPES) else "type_%d" % ptype,
                    "sampling": [xs, ys]})
    return out


def tiledesc(value):
    fields = _unpack("<IIB", value)
    if fields is None:
        return None
    xs, ys, mode = fields
    level, rounding = mode & 0x0F, (mode >> 4) & 0x0F
    return {"size": [xs, ys],
            "level_mode": LEVELS[level] if level < len(LEVELS) else "level_%d" % level,
            "rounding": "up" if rounding == 1 else "down"}


def frame_rate(value):
    fields = _unpack("<II", value)
    if fields is None or fields[1] == 0:
        return None
    return str(Fraction(fields[0], fields[1]))


def compression(attrs):
    value = attrs.get("compression", (None, None))[1]
    if not value:
        return None
    index = value[0]
    return COMPRESSIONS[index] if index < len(COMPRESSIONS) else None


def line_order(attrs):
    value = attrs.get("lineOrder", (None, None))[1]
    if not value:
        return None
    index = value[0]
    return ORDERS[index] if index < len(ORDERS) else None


def _round_log2(size, rounding):
    """floorLog2, or ceilLog2 when levels are rounded up."""
    if size < 2:
        return 0
    if rounding == "up":
        return (size - 1).bit_length()
    return size.bit_length() - 1


def _level_size(size, level, rounding):
    n = size >> level
    if rounding == "up" and (n << level) < size:
        n += 1
    return max(n, 1)


def _num_levels(width, height, mode, rounding):
    if mode == "mipmap":
        n = _round_log2(max(width, height), rounding) + 1
        return n, n
    if mode == "ripmap":
        return _round_log2(width, rounding) + 1, _round_log2(height, rounding) + 1
    return 1, 1


def chunk_count(attrs, tiles):
    value = attrs.get("dataWindow", (None, None))[1]
    window = box(value) if value is not None else None
    if window is None:
        return None
    width, height = window["size"]
    if tiles is not None:
        xs, ys = tiles["size"]
        if xs <= 0 or ys <= 0:
            return None
        rounding = tiles["rounding"]
        num_x, num_y = _num_levels(width, height, tiles["level_mode"], rounding)
        if tiles["level_mode"] == "mipmap":
            # Mipmap levels are diagonal: level l uses the same l for x and y.
            return sum(((_level_size(width, level, rounding) + xs - 1) // xs) *
                       ((_level_size(height, level, rounding) + ys - 1) // ys)
                       for level in range(num_x))
        total = 0
        for level_y in range(num_y):
            rows = (_level_size(height, level_y, rounding) + ys - 1) // ys
            for level_x in range(num_x):
                cols = (_level_size(width, level_x, rounding) + xs - 1) // xs
                total += cols * rows
        return total
    lines = LINES.get(compression(attrs))
    if lines is None:
        return None
    return (height + lines - 1) // lines


def header(data, p):
    attrs = {}
    while p < len(data) and data[p] != 0:
        name, p = cstr(data, p)
        typ, p = cstr(data, p)
        fields = _unpack("<i", data, p)
        if fields is None:
            raise ExrError("truncated attribute %r" % name)
        size, = fields
        p += 4
        if size < 0 or p + size > len(data):
            raise ExrError("truncated attribute %r" % name)
        attrs[name] = (typ, data[p:p + size])
        p += size
    if p >= len(data):
        raise ExrError("truncated header")
    return attrs, p + 1


def describe(attrs, tiled_file, multipart=False):
    get = lambda n: attrs.get(n, (None, None))[1]
    tiles = tiledesc(get("tiles")) if get("tiles") is not None else None
    name, typ = get("name"), get("type")
    if typ is not None:
        typ = string(typ)
    elif not multipart:
        typ = "tiledimage" if tiled_file else "scanlineimage"
    aspect = _unpack("<f", get("pixelAspectRatio")) if get("pixelAspectRatio") is not None else None
    fps = get("framesPerSecond")
    part = {"name": string(name) if name is not None else None,
            "type": typ,
            "channels": channels(get("channels")) if get("channels") is not None else [],
            "compression": compression(attrs),
            "data_window": box(get("dataWindow")) if get("dataWindow") is not None else None,
            "display_window": box(get("displayWindow")) if get("displayWindow") is not None else None,
            "line_order": line_order(attrs),
            "pixel_aspect": round(aspect[0], 6) if aspect is not None else None,
            "tiles": tiles,
            "chunks": chunk_count(attrs, tiles),
            "frame_rate": frame_rate(fps) if fps is not None else None,
            "custom": sorted(attr for attr in attrs if attr not in KNOWN)}
    return part


def parse(data):
    if data[:4] != MAGIC:
        raise ExrError("not an OpenEXR file")
    v, = struct.unpack_from("<I", data, 4)
    multipart = bool(v & MULTIPART_FLAG)
    out = {"version": v & VERSION_NUMBER_FIELD, "tiled": bool(v & TILED_FLAG),
           "long_names": bool(v & LONG_NAMES_FLAG), "deep": bool(v & NON_IMAGE_FLAG),
           "multipart": multipart, "parts": []}
    p = 8
    while True:
        attrs, p = header(data, p)
        out["parts"].append(describe(attrs, out["tiled"], multipart))
        # A multi-part file terminates the list of headers with an empty header;
        # a single-part file goes straight on to its chunk offset table.
        if not multipart or p >= len(data) or data[p] == 0:
            break
    return out
