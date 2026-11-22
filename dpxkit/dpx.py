"""DPX (SMPTE ST 268) image file headers: file, image elements, film and TV industry headers."""
import math
import re
import struct

DESCRIPTORS = {1: "R", 2: "G", 3: "B", 4: "A", 6: "Y", 50: "RGB", 51: "RGBA", 52: "ABGR",
               100: "CbYCrY", 101: "CbYACrYA", 102: "CbYCr", 103: "CbYCrA"}
COMPONENTS = {1: 1, 2: 1, 3: 1, 4: 1, 6: 1, 50: 3, 51: 4, 52: 4, 100: 2, 101: 3, 102: 3, 103: 4}
PACKING = {0: "packed", 1: "filled_a", 2: "filled_b"}
UNDEF8 = 0xFF
UNDEF16 = 0xFFFF
UNDEF32 = 0xFFFFFFFF

# SMPTE ST 268 header offsets.
HEADER_SIZE = 2048
IMAGE_OFFSET = 4
VERSION = 8
FILE_SIZE = 16
CREATED = 136
CREATOR = 160
ENCRYPTION_KEY = 660
IMAGE_ORIENTATION = 768
ELEMENT_COUNT = 770
PIXEL_WIDTH = 772
PIXEL_HEIGHT = 776
ELEMENT_HEADER = 780
ELEMENT_SIZE = 72
MAX_ELEMENTS = 8
FILM_HEADER = 1664
FILM_FRAME_RATE = FILM_HEADER + 60      # R32, frames per second
TV_HEADER = 1920
TV_TIMECODE = TV_HEADER                 # U32, BCD hours, minutes, seconds, frames
TV_INTERLACE = TV_HEADER + 8            # U8, 0 = non-interlaced, 1 = interlaced, 255 = undefined
TV_FRAME_RATE = TV_HEADER + 20          # R32, frames per second


class DpxError(ValueError):
    pass


def _u16(v):
    return None if v == UNDEF16 else v


def _u32(v):
    return None if v == UNDEF32 else v


def _byte(v):
    return None if v == UNDEF8 else v


def _f(raw, e):
    """R32 field; SMPTE ST 268 marks it undefined with 0xFFFFFFFF, which unpacks to NaN."""
    v, = struct.unpack(e + "f", raw)
    return round(v, 3) if math.isfinite(v) else None


def _rate(raw, e):
    """Frame rate; zero, non-finite and out-of-range values are undefined."""
    v = _f(raw, e)
    return v if v is not None and 1 <= v <= 1000 else None


def _str(raw):
    """Text field; all-0x00 and all-0xFF both mean undefined."""
    s = raw.split(b"\0")[0].rstrip(b"\xff").strip().decode("latin-1").strip()
    return s or None


def _bcd(v):
    return (v >> 4) * 10 + (v & 15)


def _timecode(v):
    """U32 time code, one BCD byte each for hours, minutes, seconds and frames."""
    if v is None:
        return None
    fields = ((v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
    if any((b >> 4) > 9 or (b & 15) > 9 for b in fields):
        return None                     # not BCD
    hh, mm, ss, ff = (_bcd(b) for b in fields)
    if hh > 23 or mm > 59 or ss > 59 or ff > 59:
        return None                     # not a valid clock value
    return "%02d:%02d:%02d:%02d" % (hh, mm, ss, ff)


def line_bytes(width, components, depth, packing, eol=0):
    """Bytes in one image line, from the component packing, plus end-of-line padding."""
    pixels = width * components
    if depth in (8, 16):
        n = pixels * depth // 8             # byte-sized components
    elif packing == 0:
        n = math.ceil(pixels * depth / 32) * 4   # packed: bit-contiguous, word-sized lines
    elif depth == 10:
        n = math.ceil(pixels / 3) * 4       # filled: three 10-bit components per word
    elif depth == 12:
        n = pixels * 2                      # filled: one 12-bit component per word
    else:
        n = math.ceil(pixels * depth / 32) * 4
    return n + (eol or 0)


def parse(data):
    if len(data) < HEADER_SIZE:
        raise DpxError("DPX header is shorter than 2048 bytes")
    if data[:4] == b"SDPX":
        e = ">"                         # big-endian
    elif data[:4] == b"XPDS":
        e = "<"                         # little-endian (e.g. Windows workstations)
    else:
        raise DpxError("not a DPX file")

    u16 = lambda off: struct.unpack_from(e + "H", data, off)[0]
    u32 = lambda off: struct.unpack_from(e + "I", data, off)[0]

    out = {"endian": "big" if e == ">" else "little",
           "version": _str(data[VERSION:VERSION + 8]),
           "image_offset": _u32(u32(IMAGE_OFFSET)),
           "file_size": _u32(u32(FILE_SIZE)),
           "created": None,
           "creator": _str(data[CREATOR:CREATOR + 100]),
           "encrypted": u32(ENCRYPTION_KEY) != UNDEF32,
           "orientation": _u16(u16(IMAGE_ORIENTATION)),
           "width": _u32(u32(PIXEL_WIDTH)),
           "height": _u32(u32(PIXEL_HEIGHT)),
           "elements": [],
           "frame_rate": None,
           "timecode": None,
           "interlaced": None,
           "truncated": False}

    stamp = _str(data[CREATED:CREATED + 24])
    if stamp:
        m = re.match(r"(\d{4})[:\-](\d{2})[:\-](\d{2})[:\-](\d{2}):(\d{2}):(\d{2})", stamp)
        if m:
            out["created"] = "%s-%s-%sT%s:%s:%s" % m.groups()

    count = _u16(u16(ELEMENT_COUNT)) or 0
    for i in range(min(count, MAX_ELEMENTS)):
        b = ELEMENT_HEADER + ELEMENT_SIZE * i
        desc = _byte(data[b + 20])
        depth = _byte(data[b + 23])
        packing = u16(b + 24)
        encoding = u16(b + 26)
        offset = _u32(u32(b + 28))
        eol = _u32(u32(b + 32)) or 0
        descriptor = None if desc is None else DESCRIPTORS.get(desc, "type_%d" % desc)
        packing_name = PACKING.get(packing, None if packing == UNDEF16 else str(packing))
        size = None
        if (encoding == 0 and desc in COMPONENTS and packing in PACKING
                and None not in (out["width"], out["height"], depth)):
            size = line_bytes(out["width"], COMPONENTS[desc], depth, packing, eol) * out["height"]
        out["elements"].append({
            "descriptor": descriptor,
            "bit_depth": depth,
            "packing": packing_name,
            "rle": encoding == 1,
            "offset": offset,
            "data_bytes": size,
        })
        if size is not None and offset is not None and offset + size > len(data):
            out["truncated"] = True

    film_rate = _rate(data[FILM_FRAME_RATE:FILM_FRAME_RATE + 4], e)
    tv_rate = _rate(data[TV_FRAME_RATE:TV_FRAME_RATE + 4], e)
    out["frame_rate"] = film_rate if film_rate is not None else tv_rate

    out["timecode"] = _timecode(_u32(u32(TV_TIMECODE)))

    interlace = data[TV_INTERLACE]
    out["interlaced"] = None if interlace == UNDEF8 else bool(interlace)
    return out
