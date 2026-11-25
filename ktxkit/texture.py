"""KTX 1.1 textures (.ktx): header, key/value data, mip levels and texels."""
import struct

ID = b"\xabKTX 11\xbb\r\n\x1a\n"
HEADER_LEN = 64

# glType / glFormat / sized internal values used by the uncompressed exporters.
UBYTE = 0x1401
USHORT_565 = 0x8363
USHORT_5551 = 0x8034
USHORT_4444 = 0x8033
USHORT_565_REV = 0x8364
USHORT_4444_REV = 0x8365
USHORT_5551_REV = 0x8366

RED = 0x1903
RG = 0x8227
ALPHA = 0x1906
RGB = 0x1907
RGBA = 0x1908
BGR = 0x80E0
BGRA = 0x80E1
LUMINANCE = 0x1909
LUMINANCE_ALPHA = 0x190A

# Bytes per texel for the GL_UNSIGNED_BYTE formats above.
UBYTE_BPP = {
    RED: 1,
    RG: 2,
    RGB: 3,
    BGR: 3,
    RGBA: 4,
    BGRA: 4,
    LUMINANCE: 1,
    LUMINANCE_ALPHA: 2,
    ALPHA: 1,
}

# Channel layout for GL_UNSIGNED_BYTE textures: each entry maps the (R, G, B, A)
# output to a byte index in the file. None means OpenGL's default channel
# (0 for R/G/B, 1 for A); "L" repeats the luminance byte into R, G and B.
UBYTE_LAYOUTS = {
    RED: (0, 0, 0, None),
    RG: (0, 1, None, None),
    RGB: (0, 1, 2, None),
    BGR: (2, 1, 0, None),
    RGBA: (0, 1, 2, 3),
    BGRA: (2, 1, 0, 3),
    LUMINANCE: ("L", "L", "L", None),
    LUMINANCE_ALPHA: ("L", "L", "L", 1),
    ALPHA: (None, None, None, 0),
}

# Packed 16-bit layouts. Each tuple gives (mask, shift) for the R, G, B, A
# channels as OpenGL defines them for the non-_REV types.
def _field(mask):
    shift = 0
    while ((mask >> shift) & 1) == 0:
        shift += 1
    return mask, shift


_F565 = (_field(0xF800), _field(0x07E0), _field(0x001F), None)
_F5551 = (_field(0xF800), _field(0x07C0), _field(0x003E), _field(0x0001))
_F4444 = (_field(0xF000), _field(0x0F00), _field(0x00F0), _field(0x000F))
# _REV reverses the byte order of the non-REV layout: the 16-bit word read in
# the file's byte order has the components in swapped bytes, i.e. the word is
# byte-swapped relative to the non-_REV bit layout.
_R565 = (_field(0x001F), _field(0x07E0), _field(0xF800), None)
_R5551 = (_field(0x03E0), _field(0x003E), _field(0xF800), _field(0x0001))
_R4444 = (_field(0x000F), _field(0x00F0), _field(0x0F00), _field(0xF000))

PACKED_LAYOUTS = {
    USHORT_565: _F565,
    USHORT_5551: _F5551,
    USHORT_4444: _F4444,
    USHORT_565_REV: _R565,
    USHORT_5551_REV: _R5551,
    USHORT_4444_REV: _R4444,
}


class KtxError(ValueError):
    pass


def _scale(value, maximum):
    """round(value * 255 / maximum), half rounded up so 1/1 -> 255."""
    return (2 * value * 255 + maximum) // (2 * maximum)


def _make_texel_reader(gltype, glformat, endian):
    """Return (bytes_per_texel, decode(off) -> "#rrggbbaa" | None).

    Returns (None, None) for compressed textures and any type/format the
    exporters do not use for uncompressed 8/16-bit data.
    """
    if gltype == UBYTE:
        layout = UBYTE_LAYOUTS.get(glformat)
        if layout is None:
            return None, None
        bpp = UBYTE_BPP[glformat]

        def decode_ubyte(data, off, limit):
            if off + bpp > limit:
                return None
            raw = data[off:off + bpp]
            channels = []
            for index, channel in enumerate(layout):
                if channel is None:
                    # Missing R/G/B read as 0; a missing alpha reads as 1.
                    channels.append(0xFF if index == 3 else 0)
                elif channel == "L":
                    channels.append(raw[0])
                else:
                    channels.append(raw[channel])
            return "#%02x%02x%02x%02x" % tuple(channels)

        return bpp, decode_ubyte

    packed = PACKED_LAYOUTS.get(gltype)
    if packed is not None:
        word_char = endian + "H"

        def decode_packed(data, off, limit):
            if off + 2 > limit:
                return None
            word = struct.unpack_from(word_char, data, off)[0]
            out = []
            for field in packed:
                if field is None:
                    out.append(0xFF)
                    continue
                mask, shift = field
                value = (word & mask) >> shift
                out.append(_scale(value, mask >> shift))
            return "#%02x%02x%02x%02x" % tuple(out)

        return 2, decode_packed

    return None, None


def parse(data):
    if data[:12] != ID or len(data) < HEADER_LEN:
        raise KtxError("not a KTX 1.1 file")
    mark = data[12:16]
    if mark == b"\x01\x02\x03\x04":
        endian = "<"
    elif mark == b"\x04\x03\x02\x01":
        endian = ">"
    else:
        raise KtxError("unsupported endianness %r" % mark)

    (_mark, gltype, _typesize, glformat, internal, _base,
     w, h, d, arr, faces, mips, kvlen) = struct.unpack_from(endian + "13I", data, 12)
    if w == 0 or faces not in (1, 6):
        raise KtxError("unsupported texture")

    # ---- key/value data --------------------------------------------------
    key_values = {}
    p = HEADER_LEN
    kv_end = HEADER_LEN + kvlen
    while p + 4 <= kv_end:
        blob_len = struct.unpack_from(endian + "I", data, p)[0]
        blob = data[p + 4:p + 4 + blob_len]
        # Each entry is padded to a 4-byte boundary.
        p += 4 + blob_len + ((-blob_len) % 4)
        key, sep, value = blob.partition(b"\0")
        if sep and value.endswith(b"\0"):
            value = value[:-1]
        key_values[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    p = kv_end

    bpp, decode_texel = _make_texel_reader(gltype, glformat, endian)
    eff_array = max(1, arr)
    levels = []
    first_texels = []
    last_texel = None
    smallest_texel = None
    complete = True
    levels_present = 0

    for level in range(max(1, mips)):
        if p + 4 > len(data):
            complete = False
            break
        image_size = struct.unpack_from(endian + "I", data, p)[0]
        p += 4
        lw = max(1, w >> level)
        lh = max(1, h >> level)
        ld = max(1, d >> level)
        levels.append([lw, lh, image_size])
        levels_present += 1

        # imageSize is the bytes of one image (one face / slice / element).
        image_count = eff_array * faces * ld
        data_start = p
        data_end = p + image_size * image_count
        # Texels are only valid inside the bytes actually present.
        limit = min(data_end, len(data))
        if data_end > len(data):
            complete = False

        if bpp is not None:
            bases = [data_start + i * image_size for i in range(image_count)]
            if level == 0:
                first_texels = [decode_texel(data, base, limit)
                                for base in bases[:8]]
                last_base = bases[-1]
                last_off = last_base + (lh - 1) * lw * bpp + (lw - 1) * bpp
                last_texel = decode_texel(data, last_off, limit)
            smallest_texel = decode_texel(data, bases[0], limit)

        # Advance past the level and its padding; clamp to the file length so
        # a truncated level cannot make the next iteration read past EOF.
        p = min(data_end, len(data))
        p += (-p) % 4

    return {
        "endianness": "little" if endian == "<" else "big",
        "gl_type": gltype,
        "gl_format": glformat,
        "gl_internal_format": internal,
        "width": w,
        "height": h,
        "depth": d,
        "array_elements": arr,
        "faces": faces,
        "mip_levels": levels_present,
        "key_values": key_values,
        "levels": levels,
        "complete": complete,
        "first_texels": first_texels,
        "last_texel": last_texel,
        "smallest_texel": smallest_texel,
    }

