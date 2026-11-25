"""Sun raster images (.ras): header, colour map and image data.

The layout is the one described by Sun's rasterfile(5)::

    8 big-endian 32-bit words   magic, width, height, depth, length,
                                type, maptype, maplength
    colour map                  maplength bytes
    image data                  length bytes

Every row is padded to a 16-bit boundary, so a row occupies an even number
of bytes.  In the old, standard and byte-encoded types a 24-bit pixel is
stored blue, green, red and a 32-bit pixel is stored X, blue, green, red;
the RGB type stores red, green, blue (X, red, green, blue when 32-bit).
"""
import struct

MAGIC = 0x59A66A95

RT_OLD = 0
RT_STANDARD = 1
RT_BYTE_ENCODED = 2
RT_FORMAT_RGB = 3

RMT_NONE = 0
RMT_EQUAL_RGB = 1

_ESCAPE = 0x80
_DEPTHS = (1, 8, 24, 32)
_TYPES = (RT_OLD, RT_STANDARD, RT_BYTE_ENCODED, RT_FORMAT_RGB)


class RasterError(ValueError):
    pass


def _decode_rle(data, limit):
    """Expand byte-encoded (RLE) image data.

    ``80 00`` stands for a literal 0x80 byte, ``80 count value`` for
    ``count + 1`` copies of ``value``.  At most ``limit`` bytes are
    produced, so a truncated or padded stream cannot run away.
    """
    out = bytearray()
    i, size = 0, len(data)
    while i < size and len(out) < limit:
        byte = data[i]
        if byte != _ESCAPE:
            out.append(byte)
            i += 1
            continue
        if i + 1 >= size:
            break
        count = data[i + 1]
        if count == 0:
            out.append(_ESCAPE)
            i += 2
            continue
        if i + 2 >= size:
            break
        out.extend(bytes((data[i + 2],)) * (count + 1))
        i += 3
    del out[limit:]
    return bytes(out)


def _colormap(maptype, maplen, raw):
    """Return the colour map as RGB tuples, or None when there is none.

    RMT_EQUAL_RGB stores the map as three planes - all red entries, then
    green, then blue - with maplen / 3 entries in each.
    """
    if maptype != RMT_EQUAL_RGB or maplen < 3:
        return None
    available = min(maplen, len(raw))
    entries = available // 3
    if entries == 0:
        return None
    red = raw[:entries]
    green = raw[entries:2 * entries]
    blue = raw[2 * entries:3 * entries]
    return list(zip(red, green, blue))


def _row_colors(line, width, depth, typ, cmap):
    """Yield the displayed (red, green, blue) colour of each pixel."""
    if depth == 1:
        for x in range(width):
            value = (line[x >> 3] >> (7 - (x & 7))) & 1
            if cmap is not None and value < len(cmap):
                yield cmap[value]
            else:
                yield (0, 0, 0) if value else (255, 255, 255)
    elif depth == 8:
        for x in range(width):
            value = line[x]
            if cmap is None:
                yield (value, value, value)
            elif value < len(cmap):
                yield cmap[value]
            else:
                yield (0, 0, 0)
    elif depth == 24:
        for x in range(width):
            first, second, third = line[3 * x:3 * x + 3]
            if typ == RT_FORMAT_RGB:
                yield (first, second, third)
            else:
                yield (third, second, first)
    else:  # 32-bit, the leading byte is not displayed
        for x in range(width):
            _, second, third, fourth = line[4 * x:4 * x + 4]
            if typ == RT_FORMAT_RGB:
                yield (second, third, fourth)
            else:
                yield (fourth, third, second)


def parse(data):
    if len(data) < 32:
        raise RasterError("not a Sun raster image")
    magic, width, height, depth, length, typ, maptype, maplen = struct.unpack_from(">8I", data)
    if magic != MAGIC or width == 0 or height == 0:
        raise RasterError("not a Sun raster image")
    if depth not in _DEPTHS:
        raise RasterError("unsupported depth %d" % depth)
    if typ not in _TYPES:
        raise RasterError("unsupported type %d" % typ)

    cmap = _colormap(maptype, maplen, data[32:32 + maplen])
    body = data[32 + maplen:]
    if 0 < length <= len(body):
        body = body[:length]
    row_bytes = ((width * depth + 15) // 16) * 2
    needed = row_bytes * height
    if typ == RT_BYTE_ENCODED:
        body = _decode_rle(body, needed)
    else:
        body = body[:needed]

    rows = min(height, len(body) // row_bytes)
    seen = set()
    first_row = []
    last_pixel = None
    for y in range(rows):
        line = body[y * row_bytes:(y + 1) * row_bytes]
        for x, colour in enumerate(_row_colors(line, width, depth, typ, cmap)):
            pixel = "#%02x%02x%02x" % colour
            seen.add(pixel)
            if y == 0 and x < 8:
                first_row.append(pixel)
            last_pixel = pixel
    return {"width": width, "height": height, "depth": depth, "type": typ,
            "colormap_entries": len(cmap) if cmap is not None else 0,
            "row_bytes": row_bytes, "rows": rows, "complete": rows == height,
            "colors_used": len(seen), "first_row": first_row, "last_pixel": last_pixel}
