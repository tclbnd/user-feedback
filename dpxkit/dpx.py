"""DPX (SMPTE ST 268) image file headers: file, image elements, film and TV industry headers."""
import math
import re
import struct
from fractions import Fraction

# Image element descriptors and the samples each stores per pixel.
DESCRIPTORS = {1: "R", 2: "G", 3: "B", 4: "A", 6: "Y", 50: "RGB", 51: "RGBA", 52: "ABGR",
               100: "CbYCrY", 101: "CbYACrYA", 102: "CbYCr", 103: "CbYCrA"}
COMPONENTS = {1: 1, 2: 1, 3: 1, 4: 1, 6: 1, 50: 3, 51: 4, 52: 4, 100: 2, 101: 3, 102: 3, 103: 4}
PACKINGS = ("packed", "filled_a", "filled_b")
TRANSFERS = ("user_defined", "printing_density", "linear", "logarithmic", "unspecified_video", "smpte_274m",
             "itu_r_709", "itu_r_601_625", "itu_r_601_525", "ntsc_composite", "pal_composite", "z_linear",
             "z_homogeneous")

UNDEF32 = 0xFFFFFFFF
UNDEF16 = 0xFFFF
UNDEF8 = 0xFF

# The magic reads "SDPX"; a little-endian file stores the same bytes reversed.
MAGICS = {b"SDPX": ">", b"XPDS": "<"}

# Header sections: file, image and image source information make up the generic
# header, the film and television information the industry header.
GENERIC_HEADER = 1664
INDUSTRY_HEADER = 384
# The image element descriptions, eight of them, start the image header.
ELEMENTS_AT = 780
ELEMENT_SIZE = 72
# The television header has a frame rate field of its own, at 1932.  Some
# writers leave it blank and put the rate in the gamma slot at 1940 instead, so
# that slot is read too, but only when it holds a plausible rate and not a gamma.
TV_RATE_AT = 1932
TV_GAMMA_AT = 1940
PLAUSIBLE_RATE = (8, 120)

CREATED = re.compile(r"^(\d{4})[-:/](\d{2})[-:/](\d{2})[-:/T ](\d{2}):(\d{2}):(\d{2})")


class DpxError(ValueError):
    pass


def _u32(v):
    return None if v is None or v == UNDEF32 else v


def _u16(v):
    return None if v is None or v == UNDEF16 else v


def _u8(v):
    return None if v is None or v == UNDEF8 else v


def _text(raw):
    """A header string; blank and 0xFF-filled fields are undefined."""
    if raw is None:
        return None
    s = raw.split(b"\0", 1)[0].decode("latin-1").strip("\xff").strip()
    return s or None


def _created(raw):
    """The creation date and time as YYYY-MM-DDTHH:MM:SS."""
    s = _text(raw)
    if s is None:
        return None
    m = CREATED.match(s)
    if m is None:
        return None
    year, month, day, hour, minute, second = (int(part) for part in m.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 60):
        return None
    return "%04d-%02d-%02dT%02d:%02d:%02d" % (year, month, day, hour, minute, second)


def _float(raw, e):
    """A float field, None when the file leaves it undefined or holds a NaN."""
    if raw is None or raw == b"\xff\xff\xff\xff":
        return None
    value, = struct.unpack(e + "f", raw)
    return value if math.isfinite(value) else None


def _rate(value):
    """A frame rate; undefined, NaN and non-positive rates have no value."""
    if value is None or not value > 0:
        return None
    value = round(value, 3)
    return value if value > 0 else None


def _timecode(value):
    """A timecode as (hours, minutes, seconds, frames), or None when undefined.

    ST 268 stores the four digits packed decimal, one in each half-byte; a
    value with a non-decimal digit, or with a field out of range, is not a
    timecode at all.
    """
    if value is None or value == UNDEF32:
        return None
    fields = []
    for byte in (value >> 24, value >> 16 & 0xFF, value >> 8 & 0xFF, value & 0xFF):
        high, low = byte >> 4, byte & 0x0F
        if high > 9 or low > 9:
            return None
        fields.append(high * 10 + low)
    if fields[0] > 23 or fields[1] > 59 or fields[2] > 59:
        return None
    return fields


def line_bytes(width, components, depth, packing, eol=0):
    """Bytes of one line, with the packing method's fill and the end-of-line padding."""
    if not depth:
        return eol or 0
    samples = width * components
    if packing == 0:
        # Packed: one continuous bit stream, each line starting on a 32-bit word.
        size = (samples * depth + 31) // 32 * 4
    else:
        # Filled methods A and B: whole samples in each 32-bit word, or 64-bit
        # word for depths beyond 32 bits.
        word = 8 if depth > 32 else 4
        per_word = max(1, word * 8 // depth)
        size = (samples + per_word - 1) // per_word * word
    return size + (eol or 0)


def parse(data):
    if len(data) < 4:
        raise DpxError("file is too short to hold a DPX header")
    e = MAGICS.get(bytes(data[:4]))
    if e is None:
        raise DpxError("not a DPX file")

    def raw(off, size):
        # A header that stops early leaves the fields behind it undefined.
        return bytes(data[off:off + size]) if off >= 0 and off + size <= len(data) else None

    def u32(off):
        b = raw(off, 4)
        return struct.unpack(e + "I", b)[0] if b is not None else None

    def u16(off):
        b = raw(off, 2)
        return struct.unpack(e + "H", b)[0] if b is not None else None

    def u8(off):
        b = raw(off, 1)
        return b[0] if b is not None else None

    # The file information header tells how much room the file keeps for its
    # headers; the film and television fields do not exist past that room.
    generic = _u32(u32(24)) or GENERIC_HEADER
    industry = _u32(u32(28))
    header_end = generic + (INDUSTRY_HEADER if industry is None else industry)

    def industry_u32(off):
        return _u32(u32(off)) if off + 4 <= header_end else None

    def industry_f32(off):
        return _float(raw(off, 4), e) if off + 4 <= header_end else None

    def industry_u8(off):
        return _u8(u8(off) if off + 1 <= header_end else None)

    orientation = _u16(u16(768))
    width = _u32(u32(772))
    height = _u32(u32(776))
    # A key of 0xFFFFFFFF, or an undefined one, means the frame is not encrypted.
    key = u32(660)

    # Element 0 describes the image data; its descriptor decides how many
    # samples the image header's width counts per line.
    count = u16(770)
    if count is None or count in (0, UNDEF16):
        count = 1
    elements = []
    truncated = False
    for i in range(min(count, 8)):
        base = ELEMENTS_AT + ELEMENT_SIZE * i
        if raw(base, ELEMENT_SIZE) is None:
            break
        descriptor = _u8(u8(base + 20))
        transfer = _u8(u8(base + 21))
        sign = _u8(u8(base))
        depth = _u8(u8(base + 23))
        packing = _u16(u16(base + 24))
        encoding = _u16(u16(base + 26))
        offset = _u32(u32(base + 28))
        # The end-of-line padding is a 32-bit count; an undefined one is none.
        eol = _u32(u32(base + 32)) or 0

        size = None
        if (None not in (width, height, depth, descriptor) and depth > 0
                and encoding in (0, None) and packing in (0, 1, 2)):
            size = line_bytes(width, COMPONENTS.get(descriptor, 1), depth, packing, eol) * height
        elements.append({
            "descriptor": DESCRIPTORS.get(descriptor, "type_%s" % descriptor)
                          if descriptor is not None else None,
            "bit_depth": depth,
            "signed": None if sign is None else sign == 1,
            "transfer": (TRANSFERS[transfer] if transfer < len(TRANSFERS) else "type_%s" % transfer)
                        if transfer is not None else None,
            "packing": PACKINGS[packing] if packing in (0, 1, 2) else packing,
            "rle": None if encoding is None else encoding == 1,
            "offset": offset,
            "data_bytes": size,
        })
        if size is not None and offset is not None and offset + size > len(data):
            truncated = True

    if width is None or height is None:
        display_width = display_height = None
    # Orientations 4-7 store the picture transposed in the image header.
    elif orientation in (4, 5, 6, 7):
        display_width, display_height = height, width
    else:
        display_width, display_height = width, height

    # The image source header holds the pixel aspect ratio, horizontal first.
    aspect_h, aspect_v = _u32(u32(1628)), _u32(u32(1632))
    if aspect_h and aspect_v:
        ratio = Fraction(aspect_h, aspect_v)
        pixel_aspect = "%d/%d" % (ratio.numerator, ratio.denominator)
    else:
        pixel_aspect = None

    # The film header holds the rate of the scanned film; the television header
    # that of the video a transfer made of it.
    frame_rate = _rate(industry_f32(1724))
    if frame_rate is None:
        frame_rate = _tv_rate(industry_f32)

    timecode = None
    fields = _timecode(industry_u32(1920))
    if fields is not None:
        timecode = "%02d:%02d:%02d:%02d" % tuple(fields)

    interlaced = industry_u8(1928)

    return {
        "endian": "big" if e == ">" else "little",
        "version": _text(raw(8, 8)),
        "image_offset": _u32(u32(4)),
        "file_size": _u32(u32(16)),
        "created": _created(raw(136, 24)),
        "creator": _text(raw(160, 100)),
        "encrypted": key is not None and key != UNDEF32,
        "orientation": orientation,
        "width": width,
        "height": height,
        "elements": elements,
        "frame_rate": frame_rate,
        "timecode": timecode,
        "interlaced": None if interlaced is None else bool(interlaced),
        "truncated": truncated,
        "display_width": display_width,
        "display_height": display_height,
        "pixel_aspect": pixel_aspect,
        "frame_position": industry_u32(1712),
        "sequence_length": industry_u32(1716),
    }


def _tv_rate(field):
    """Frame rate from the television header.

    The rate has a field of its own; the gamma slot is only a fallback for the
    writers that leave that field blank.
    """
    rate = _rate(field(TV_RATE_AT))
    if rate is None:
        fallback = _rate(field(TV_GAMMA_AT))
        low, high = PLAUSIBLE_RATE
        if fallback is not None and low <= fallback <= high:
            rate = fallback
    return rate
