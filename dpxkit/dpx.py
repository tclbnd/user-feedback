"""DPX (SMPTE ST 268) image file headers: file, image elements, film and TV industry headers."""
import math
import re
import struct

# Image element descriptor codes (SMPTE ST 268-1, table 2).
DESCRIPTORS = {
    1: "R", 2: "G", 3: "B", 4: "A", 6: "Y",
    50: "RGB", 51: "RGBA", 52: "ABGR",
    100: "CbYCrY", 101: "CbYACrYA", 102: "CbYCr", 103: "CbYCrA",
}
# Component samples per pixel for each descriptor.  The 4:2:2 formats carry
# two samples per pixel and the 4:2:2:4 formats three, even though a pixel
# group spans two luminance samples.
COMPONENTS = {
    1: 1, 2: 1, 3: 1, 4: 1, 6: 1,
    50: 3, 51: 4, 52: 4,
    100: 2, 101: 3, 102: 3, 103: 4,
}
PACKINGS = {0: "packed", 1: "filled_a", 2: "filled_b"}

UNDEF8 = 0xFF
UNDEF16 = 0xFFFF
UNDEF32 = 0xFFFFFFFF


class DpxError(ValueError):
    pass


def _u32(v):
    return None if v == UNDEF32 else v


def _u16(v):
    return None if v == UNDEF16 else v


def _u8(v):
    return None if v == UNDEF8 else v


def _f(bits, e):
    """A 32-bit float header field from its bit pattern, None where it is undefined."""
    if bits is None or bits in (UNDEF32, 0):
        return None
    value, = struct.unpack(e + "f", struct.pack(e + "I", bits))
    if not math.isfinite(value) or not 0 < value <= 1000:
        return None
    return round(value, 3)


def _str(raw):
    s = raw.split(b"\0")[0].decode("latin-1").strip("\xff").strip()
    return s or None


def _bcd(v):
    return (v >> 4) * 10 + (v & 15)


def line_bytes(width, components, depth, packing, eol=0):
    """Bytes in one image line: the packed samples plus the end-of-line padding.

    Packed data is bit-contiguous, rounded up to whole 32-bit words.  Filled
    data stores 10-bit samples three to a 32-bit word and 12-bit samples in
    16-bit containers.
    """
    if packing and depth == 10:
        size = (width * components + 2) // 3 * 4
    elif packing and depth == 12:
        size = width * components * 2
    elif depth in (8, 16):
        size = width * components * depth // 8
    else:
        size = (width * components * depth + 31) // 32 * 4
    return size + eol


def parse(data):
    if len(data) < 4:
        raise DpxError("not a DPX file")
    magic = data[:4]
    if magic == b"SDPX":
        e = ">"
    elif magic == b"XPDS":
        e = "<"
    else:
        raise DpxError("not a DPX file")
    if len(data) < 1408:
        raise DpxError("DPX header is shorter than the image header")

    # The generic header length covers the file and image headers; the
    # industry header length reserves room for the film and television
    # headers that follow.  Fields past the end of a short or undeclared
    # header do not exist.
    generic = struct.unpack_from(e + "I", data, 24)[0]
    industry = struct.unpack_from(e + "I", data, 28)[0]
    end = len(data)
    if generic != UNDEF32 and industry != UNDEF32 and generic >= 1408:
        end = min(end, generic + industry)

    def field(fmt, off, size):
        """Decode a header field, or None when the header leaves no room for it."""
        if off + size > end:
            return None
        return struct.unpack_from(e + fmt, data, off)[0]

    u32 = lambda off: field("I", off, 4)
    u16 = lambda off: field("H", off, 2)

    created = None
    stamp = _str(data[136:160])
    if stamp:
        m = re.match(r"^(\d{4})[:\-/](\d{2})[:\-/](\d{2})[ T:]+(\d{2}):(\d{2}):(\d{2})", stamp)
        if m:
            created = "%s-%s-%sT%s:%s:%s" % m.groups()

    orientation = _u16(u16(768))
    width = _u32(u32(772))
    height = _u32(u32(776))
    key = u32(660)

    out = {
        "endian": "big" if e == ">" else "little",
        "version": _str(data[8:16]),
        "image_offset": _u32(u32(4)),
        "file_size": _u32(u32(16)),
        "created": created,
        "creator": _str(data[160:260]),
        "encrypted": None if key is None else key != UNDEF32,
        "orientation": orientation,
        "width": width,
        "height": height,
        "elements": [],
        "frame_rate": None,
        "timecode": None,
        "interlaced": None,
        "truncated": False,
    }

    count = u16(770)
    count = 0 if count in (None, UNDEF16) else min(count, 8)
    for i in range(count):
        b = 780 + 72 * i
        if b + 72 > end:
            break
        desc = data[b + 20]
        depth = _u8(data[b + 23])
        packing = _u16(u16(b + 24))
        encoding = _u16(u16(b + 26))
        offset = _u32(u32(b + 28))
        eol = _u32(u32(b + 32)) or 0

        size = None
        if encoding == 0 and None not in (width, height, depth) and desc in COMPONENTS:
            size = line_bytes(width, COMPONENTS[desc], depth, packing, eol) * height
            if offset is not None and offset + size > len(data):
                out["truncated"] = True

        out["elements"].append({
            "descriptor": None if desc == UNDEF8 else DESCRIPTORS.get(desc, "type_%d" % desc),
            "bit_depth": depth,
            "packing": None if packing is None else PACKINGS.get(packing, str(packing)),
            "rle": None if encoding is None else encoding == 1,
            "offset": offset,
            "data_bytes": size,
        })

    if orientation in (4, 5, 6, 7) and width is not None and height is not None:
        out["display_width"], out["display_height"] = height, width
    else:
        out["display_width"], out["display_height"] = width, height

    # The film header's frame rate is authoritative; the television header
    # fills in only when the film header leaves it undefined.
    film_rate = _f(u32(1724), e)
    tv_rate = _f(u32(1940), e)
    out["frame_rate"] = film_rate if film_rate is not None else tv_rate

    tc = u32(1920)
    if tc is not None and tc != UNDEF32:
        # Each byte holds two BCD digits; the top bits of the frame byte are
        # drop-frame and colour-frame flags, not part of the count.
        out["timecode"] = "%02d:%02d:%02d:%02d" % (
            _bcd(tc >> 24 & 0xFF), _bcd(tc >> 16 & 0xFF),
            _bcd(tc >> 8 & 0xFF), _bcd(tc & 0x3F))

    il = field("B", 1928, 1)
    out["interlaced"] = {0: False, 1: True}.get(il)

    return out
