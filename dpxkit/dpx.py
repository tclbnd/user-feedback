"""DPX (SMPTE ST 268) image file headers: file, image elements, film and TV industry headers."""
import math
from fractions import Fraction
import struct

UNDEF32 = 0xFFFFFFFF
UNDEF16 = 0xFFFF

# SMPTE ST 268 image element descriptors (Table 8).
DESCRIPTORS = {1: "R", 2: "G", 3: "B", 4: "A", 6: "Y",
               50: "RGB", 51: "RGBA", 52: "ABGR",
               100: "CbYCrY", 101: "CbYCr", 102: "CbYCrA", 103: "CbYACrYA"}
COMPONENTS = {1: 1, 2: 1, 3: 1, 4: 1, 6: 1,
              50: 3, 51: 4, 52: 4,
              100: 2, 101: 3, 102: 4, 103: 4}

# Transfer characteristics (Table 6), codes 0-12. The same names apply to the
# colorimetric specification (Table 7) for the codes it defines, 0-10.
SPEC_NAMES = ("user_defined", "printing_density", "linear", "logarithmic",
              "unspecified_video", "smpte_274m", "itu_r_709", "itu_r_601_625",
              "itu_r_601_525", "ntsc_composite", "pal_composite",
              "z_linear", "z_homogeneous")
COLORIMETRIC_MAX = 10
PACKINGS = ("packed", "filled_a", "filled_b")


class DpxError(ValueError):
    pass


def _str(raw):
    s = raw.split(b"\0")[0].decode("latin-1").strip()
    return s or None


def _bcd(b, flags_mask=0x00):
    # Flag bits (drop frame, color frame) sit above the BCD digits.
    b &= ~flags_mask & 0xFF
    return (b >> 4) * 10 + (b & 0x0F)

def line_bytes(width, components, depth, packing, eol=0):
    """Image bytes in one scan line, per the ST 268 packing method, plus EOL padding."""
    if depth == 0:
        pitch = 0
    elif packing == 0:       # packed: samples bit packed, line padded to a 32-bit word
        pitch = 4 * math.ceil(width * components * depth / 32)
    elif packing == 1:       # filled method A: components packed into 32-bit words
        per_word = max(32 // depth, 1)
        pitch = 4 * math.ceil(width * components / per_word)
    elif packing == 2:       # filled method B: samples packed into 32-bit words (word-level padding)
        words = 4 if depth == 10 else (2 if depth == 12 else 1)
        pitch = 4 * width * math.ceil(components / words)
    else:
        return None
    return pitch + (eol or 0)


def parse(data):
    if len(data) < 2048:
        raise DpxError("DPX header is shorter than 2048 bytes")
    magic = data[:4]
    if magic == b"SDPX":
        e = ">"
    elif magic == b"XPDS":
        e = "<"
    else:
        raise DpxError("not a DPX file")

    u32 = lambda off: struct.unpack_from(e + "I", data, off)[0]
    u16 = lambda off: struct.unpack_from(e + "H", data, off)[0]
    u32u = lambda off: None if u32(off) == UNDEF32 else u32(off)
    u16u = lambda off: None if u16(off) == UNDEF16 else u16(off)

    def rate(off):
        v, = struct.unpack_from(e + "f", data, off)
        # Unset rate fields are left as all-ones (NaN/inf); zero is no rate either.
        if not math.isfinite(v) or v <= 0:
            return None
        return round(v, 3)

    out = {"endian": "big" if e == ">" else "little", "version": _str(data[8:16]),
           "image_offset": u32u(4), "file_size": u32u(16), "created": None,
           "creator": _str(data[160:260]), "encrypted": u32(660) not in (0, UNDEF32),
           "orientation": u16u(768), "width": u32u(772), "height": u32u(776),
           "elements": [], "pixel_aspect": None, "frame_position": None,
           "sequence_length": None, "frame_rate": None, "timecode": None,
           "interlaced": None, "truncated": False}

    stamp = _str(data[136:160])
    if stamp and len(stamp) >= 19:
        out["created"] = stamp[:10].replace(":", "-") + "T" + stamp[11:19]

    # Orientations 4-7 store the displayed picture transposed.
    w, h = out["width"], out["height"]
    if w is None or h is None:
        out["display_width"] = out["display_height"] = None
    elif out["orientation"] in (4, 5, 6, 7):
        out["display_width"], out["display_height"] = h, w
    else:
        out["display_width"], out["display_height"] = w, h

    # Horizontal:vertical pixel aspect ratio (image source information header).
    px, py = u32(1628), u32(1632)
    if px in (0, UNDEF32) or py in (0, UNDEF32):
        out["pixel_aspect"] = None
    else:
        r = Fraction(px, py)
        out["pixel_aspect"] = "%d/%d" % (r.numerator, r.denominator)

    count = u16(770)
    if count == UNDEF16:
        count = 0
    for i in range(min(count, 8)):
        b = 780 + 72 * i
        desc, transfer, colori, depth = data[b + 20], data[b + 21], data[b + 22], data[b + 23]
        packing_raw, encoding = u16(b + 24), u16(b + 26)
        offset, eol = u32u(b + 28), u32u(b + 32)
        packing = packing_raw if packing_raw < 3 else None
        rle = {0: False, 1: True}.get(encoding)
        comps = COMPONENTS.get(desc, 1)
        size = None
        if rle is False and w is not None and h is not None and packing is not None:
            size = line_bytes(w, comps, depth, packing, eol or 0) * h
        out["elements"].append({
            "descriptor": DESCRIPTORS.get(desc, "type_%d" % desc),
            "bit_depth": depth,
            "signed": data[b] == 1,
            "transfer": SPEC_NAMES[transfer] if transfer < len(SPEC_NAMES) else "type_%d" % transfer,
            "colorimetric": SPEC_NAMES[colori] if colori <= COLORIMETRIC_MAX else "type_%d" % colori,
            "packing": PACKINGS[packing] if packing is not None else None,
            "rle": rle,
            "offset": offset,
            "data_bytes": size})
        if size is not None and offset is not None and offset + size > len(data):
            out["truncated"] = True

    # The file header says how much room (if any) the film and TV headers occupy.
    film_len, tv_len = u32u(24), u32u(28)
    film_rate = tv_rate = None
    if film_len:
        out["frame_position"] = u32u(1712)
        out["sequence_length"] = u32u(1716)
        film_rate = rate(1724)
    else:
        out["frame_position"] = out["sequence_length"] = None
    if tv_len:
        raw_tc = data[1920:1924]
        if raw_tc != b"\xff" * 4:
            # ST 268: bit 7 of hours and bits 6-7 of frames are timecode flag bits.
            hh = _bcd(raw_tc[0], 0x80)
            mm, ss = _bcd(raw_tc[1]), _bcd(raw_tc[2])
            ff = _bcd(raw_tc[3], 0xC0)
            out["timecode"] = "%02d:%02d:%02d:%02d" % (hh, mm, ss, ff)
        out["interlaced"] = {0: False, 1: True}.get(data[1928])
        tv_rate = rate(1940)
    # Film header rate takes precedence; fall back to the television header.
    out["frame_rate"] = film_rate if film_rate is not None else tv_rate
    return out
