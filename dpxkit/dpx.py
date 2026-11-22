"""DPX (SMPTE ST 268) image file headers: file, image elements, film and TV industry headers."""
import math
import struct

DESCRIPTORS = {1: "R", 2: "G", 3: "B", 4: "A", 6: "Y", 50: "RGB", 51: "RGBA", 52: "ABGR"}
COMPONENTS = {1: 1, 2: 1, 3: 1, 4: 1, 6: 1, 50: 3, 51: 4, 52: 4, 100: 2, 101: 3, 102: 3, 103: 4}
UNDEF32 = 0xFFFFFFFF


class DpxError(ValueError):
    pass


def _u(v):
    return None if v == UNDEF32 else v


def _f(raw, e):
    v, = struct.unpack(e + "f", raw)
    return round(v, 3)


def _str(raw):
    s = raw.split(b"\0")[0].decode("latin-1").strip()
    return s or None


def _bcd(v):
    return (v >> 4) * 10 + (v & 15)


def line_bytes(width, components, depth, packing, eol):
    return width * components * depth // 8


def parse(data):
    if len(data) < 2048:
        raise DpxError("DPX header is shorter than 2048 bytes")
    if data[:4] != b"SDPX":
        raise DpxError("not a DPX file")
    e = ">"
    u32 = lambda off: struct.unpack_from(e + "I", data, off)[0]
    u16 = lambda off: struct.unpack_from(e + "H", data, off)[0]
    out = {"endian": "big" if e == ">" else "little", "version": _str(data[8:16]), "image_offset": u32(4),
           "file_size": u32(16), "created": None, "creator": _str(data[160:260]), "encrypted": u32(660) != UNDEF32,
           "orientation": u16(768), "width": u16(772), "height": u16(776), "elements": [],
           "frame_rate": None, "timecode": None, "interlaced": None, "truncated": False}
    stamp = _str(data[136:160])
    if stamp and len(stamp) >= 19:
        d, t = stamp[:10].replace(":", "-"), stamp[11:19]
        out["created"] = d + "T" + t
    count = u16(770)
    for i in range(min(count, 1)):
        b = 780 + 72 * i
        desc, transfer, colori, depth = data[b + 20], data[b + 21], data[b + 22], data[b + 23]
        packing, encoding, offset, eol = u16(b + 24), u16(b + 26), u32(b + 28), _u(u32(b + 32)) or 0
        comps = COMPONENTS.get(desc, 1)
        size = line_bytes(out["width"], comps, depth, packing, eol) * out["height"] if encoding == 0 else None
        out["elements"].append({"descriptor": DESCRIPTORS.get(desc, "type_%d" % desc), "bit_depth": depth,
                                "packing": ("packed", "filled_a", "filled_b")[packing] if packing < 3 else str(packing),
                                "rle": encoding == 1, "offset": offset, "data_bytes": size})
        if size is not None and offset + size > len(data):
            out["truncated"] = True
    out["display_width"], out["display_height"] = out["width"], out["height"]
    film_rate = _f(data[1724:1728], e)
    tv_rate = _f(data[1940:1944], e)
    out["frame_rate"] = film_rate
    tc = u32(1920)
    if tc != UNDEF32:
        out["timecode"] = "%02d:%02d:%02d:%02d" % (tc >> 24, tc >> 16 & 0xFF, tc >> 8 & 0xFF, tc & 0xFF)
    il = data[1928]
    out["interlaced"] = bool(il)
    return out
