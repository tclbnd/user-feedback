"""PlayStation TIM textures (.tim): CLUT block, image block, texel colours.

Colour words use the GPU's 16-bit BGR555 layout: bit 15 is the semitransparency
flag (STP), bits 14-10 blue, 9-5 green, 4-0 red.  A 16-bit texel of 0x0000, or
a palette entry of 0x0000, is never drawn by the GPU.
"""
import struct


class TimError(ValueError):
    pass


_MAGIC = 0x10
_CLUT_FLAG = 0x8
_HEADER = 12
_BPP = {0: 4, 1: 8, 2: 16, 3: 24}
_PALETTE_COLORS = {4: 16, 8: 256}

_TRANSPARENT, _SEMI, _DRAWN = 0, 1, 2


def _w(v):
    return round(v * 255 / 31)


def _rgb(word):
    """BGR555 word to '#rrggbb'; the STP bit is not part of the colour."""
    return "#%02x%02x%02x" % (_w(word & 0x1f), _w(word >> 5 & 0x1f), _w(word >> 10 & 0x1f))


def _texel(word):
    """Displayed colour and drawing mode of a BGR555 word."""
    if word == 0x0000:
        return None, _TRANSPARENT
    return _rgb(word), _SEMI if word & 0x8000 else _DRAWN


def _read_block(data, p, name):
    if p + _HEADER > len(data):
        raise TimError("truncated %s block" % name)
    size, x, y, w, h = struct.unpack_from("<I4H", data, p)
    if size < _HEADER:
        raise TimError("bad %s block length %d" % (name, size))
    # A block length covers its 12-byte header as well as the data that follows.
    body = data[p + _HEADER:min(p + size, len(data))]
    return (x, y, w, h), body, p + size


def _read_clut(data, p, bpp):
    (x, y, w, h), body, p = _read_block(data, p, "CLUT")
    entries = list(struct.unpack_from("<%dH" % (len(body) // 2), body))
    have = min(len(entries), w * h)
    size = _PALETTE_COLORS.get(bpp)      # None for 16- and 24-bit CLUTs
    if size is None:
        colors, palettes = have, 1 if have else 0
    else:
        palettes = have // size
        colors = size if palettes else have
        if palettes == 0 and have:
            palettes = 1                 # part of the first palette only
    info = {"x": x, "y": y, "colors": colors, "palettes": palettes}
    # Pixels always take their colour from the first palette.
    return info, entries[:colors], p


def _pixel(line, xx, bpp, palette):
    """Displayed colour and drawing mode of pixel `xx` of a whole row."""
    if bpp == 24:
        o = 3 * xx
        # Bytes are stored blue, green, red.
        return "#%02x%02x%02x" % (line[o + 2], line[o + 1], line[o]), _DRAWN
    if bpp == 16:
        return _texel(struct.unpack_from("<H", line, 2 * xx)[0])
    if bpp == 8:
        index = line[xx]
    else:                                # 4-bit: two pixels per byte, low nibble first
        index = line[xx // 2] >> (4 * (xx % 2)) & 0x0f
    if index >= len(palette):            # no CLUT, or an index the palette lacks
        return None, _TRANSPARENT
    return _texel(palette[index])


def _width(bpp, words):
    if bpp == 24:
        return words * 2 // 3
    if bpp == 4:
        return words * 4
    if bpp == 8:
        return words * 2
    return words


def parse(data):
    if len(data) < 8 or struct.unpack_from("<I", data)[0] != _MAGIC:
        raise TimError("not a TIM file")
    flags = struct.unpack_from("<I", data, 4)[0]
    bpp = _BPP.get(flags & 7)
    if bpp is None:
        raise TimError("unsupported pixel mode %d" % (flags & 7))
    p = 8
    clut, palette = None, []
    if flags & _CLUT_FLAG:
        clut, palette, p = _read_clut(data, p, bpp)
    (x, y, words, h), body, _ = _read_block(data, p, "image")
    width = _width(bpp, words)
    stride = words * 2
    rows = min(h, len(body) // stride) if stride else 0

    seen, first, last = set(), [], None
    transparent, semi = 0, 0
    for yy in range(rows):
        line = body[yy * stride:(yy + 1) * stride]
        for xx in range(width):
            colour, mode = _pixel(line, xx, bpp, palette)
            if mode == _TRANSPARENT:
                transparent += 1
            elif mode == _SEMI:
                semi += 1
            if colour is not None:
                seen.add(colour)
            if yy == 0 and xx < 8:
                first.append(colour)
            last = colour
    return {"bpp": bpp, "clut": clut, "x": x, "y": y, "width": width, "height": h, "rows": rows,
            "complete": rows == h, "colors_used": len(seen), "first_row": first, "last_pixel": last,
            "transparent_pixels": transparent, "semi_transparent_pixels": semi}
