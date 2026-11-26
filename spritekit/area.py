"""RISC OS sprite files (.spr): sprite area header, sprites, palettes, masks."""
import struct

# Old RISC OS screen mode numbers and their packed pixel depths.
MODE_BPP = {
    0: 1,
    1: 2,
    2: 4,
    3: 1,
    4: 1,
    5: 2,
    6: 1,
    8: 2,
    9: 4,
    10: 2,
    11: 2,
    12: 4,
    13: 8,
    14: 4,
    15: 8,
    16: 4,
    17: 4,
    18: 8,
    19: 8,
    20: 4,
    21: 8,
    22: 4,
    23: 8,
    24: 8,
}

SPRITE_HEADER = struct.Struct("<I12s7I")
AREA_HEADER = struct.Struct("<3I")
WORD_BITS = 32


class AreaError(ValueError):
    pass


def _bpp_for_mode(mode):
    """Return bits per pixel for either an old mode or a new mode word."""
    if mode in MODE_BPP:
        return MODE_BPP[mode]

    # New-style sprite mode words keep the sprite type in bits 27..31.
    # Types 1..6 correspond to 1, 2, 4, 8, 16 and 32 bpp.
    sprite_type = (mode >> 27) & 0x1F
    if 1 <= sprite_type <= 6:
        return 1 << (sprite_type - 1)

    raise AreaError("unsupported mode %#x" % mode)


def _palette_colour(word):
    # RISC OS colour words are 0xBBGGRR00 in little-endian order.
    red = (word >> 8) & 0xFF
    green = (word >> 16) & 0xFF
    blue = (word >> 24) & 0xFF
    return "#%02x%02x%02x" % (red, green, blue)


def _expand_five_bits(value):
    return int(round(value * 255 / 31))


def _direct_colour(value, bpp):
    if bpp == 16:
        # 0BBBBBGGGGGRRRRR.
        red = _expand_five_bits(value & 0x1F)
        green = _expand_five_bits((value >> 5) & 0x1F)
        blue = _expand_five_bits((value >> 10) & 0x1F)
        return "#%02x%02x%02x" % (red, green, blue)

    if bpp in (24, 32):
        red = value & 0xFF
        green = (value >> 8) & 0xFF
        blue = (value >> 16) & 0xFF
        return "#%02x%02x%02x" % (red, green, blue)

    return None


def _read_pixel(data, base, stride, first_bit, x, y, bpp):
    """Read one little-endian, LSB-first packed pixel from a sprite row."""
    bit_offset = first_bit + x * bpp
    bit_in_byte = bit_offset & 7
    byte_offset = bit_offset >> 3
    byte_count = (bit_in_byte + bpp + 7) >> 3
    start = base + y * stride + byte_offset
    raw = data[start:start + byte_count]
    value = int.from_bytes(raw, "little") >> bit_in_byte
    return value & ((1 << bpp) - 1)


def parse(data):
    if len(data) < 12:
        raise AreaError("not a sprite file")

    count, first, free = AREA_HEADER.unpack_from(data)
    if first < 16 or count > 4096:
        raise AreaError("not a sprite file")

    # A file is a sprite area with its first word (the area size) omitted.
    # The stored first-sprite offset is relative to the original area start,
    # so subtract the omitted four bytes.
    p = first - 4
    out = []
    complete = True

    for _ in range(count):
        if p < 0 or p + SPRITE_HEADER.size > len(data):
            complete = False
            break

        (nxt, raw_name, width_words, height_minus_one, first_bit, last_bit,
         image_offset, mask_offset, mode) = SPRITE_HEADER.unpack_from(data, p)

        name = raw_name.split(b"\0", 1)[0].decode("latin-1").rstrip()
        bpp = _bpp_for_mode(mode)
        height = height_minus_one + 1

        # width_words is the number of row words minus one.  first_bit and
        # last_bit are inclusive bit positions in the first and last row word.
        bit_count = width_words * WORD_BITS + last_bit + 1 - first_bit
        width = bit_count // bpp
        stride = (width_words + 1) * 4

        palette_entries = max(0, (image_offset - SPRITE_HEADER.size) // 8)
        has_mask = mask_offset > image_offset

        # Masks are one-bit bitmaps, word aligned.  The next-sprite offset
        # also gives the stored mask block length, so use it to preserve any
        # row padding used by a writer.  Fall back to the minimum row width.
        minimum_mask_stride = ((width + WORD_BITS - 1) // WORD_BITS) * 4
        mask_stride = minimum_mask_stride
        if has_mask and height:
            mask_block_size = nxt - mask_offset
            if mask_block_size > 0 and mask_block_size % height == 0:
                # Trust the writer's actual row stride; this also handles
                # padded mask rows while remaining correct for tight words.
                mask_stride = mask_block_size // height

        image_available = (
            image_offset >= SPRITE_HEADER.size
            and p + image_offset + stride * height <= len(data)
        )
        mask_available = True
        if has_mask:
            mask_available = (
                mask_offset >= SPRITE_HEADER.size
                and p + mask_offset + mask_stride * height <= len(data)
            )

        if not image_available or not mask_available:
            complete = False
            out.append({
                "name": name,
                "width": width,
                "height": height,
                "bpp": bpp,
                "has_mask": has_mask,
                "palette_entries": palette_entries,
                "first_row": None,
                "last_pixel": None,
                "transparent_pixels": None,
            })
            if nxt <= 0:
                break
            p += nxt
            continue

        palette = []
        palette_base = p + SPRITE_HEADER.size
        for index in range(palette_entries):
            word = struct.unpack_from("<I", data, palette_base + index * 8)[0]
            palette.append(_palette_colour(word))

        image_base = p + image_offset

        def colour(x, y):
            value = _read_pixel(
                data, image_base, stride, first_bit, x, y, bpp
            )
            direct = _direct_colour(value, bpp)
            if direct is not None:
                return direct
            if value < len(palette):
                return palette[value]
            return None

        first_row = [colour(x, 0) for x in range(min(8, width))]
        last_pixel = colour(width - 1, height - 1)

        transparent_pixels = 0
        if has_mask:
            mask_base = p + mask_offset
            for y in range(height):
                row_start = mask_base + y * mask_stride
                for x in range(width):
                    byte = data[row_start + (x >> 3)]
                    bit = (byte >> (x & 7)) & 1
                    if bit == 0:
                        transparent_pixels += 1

        out.append({
            "name": name,
            "width": width,
            "height": height,
            "bpp": bpp,
            "has_mask": has_mask,
            "palette_entries": palette_entries,
            "first_row": first_row,
            "last_pixel": last_pixel,
            "transparent_pixels": transparent_pixels,
        })

        if nxt <= 0:
            break
        p += nxt

    return {
        "sprites_declared": count,
        "complete": complete,
        "sprites": out,
    }
