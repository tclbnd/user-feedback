"""SGI image files (format 1.0): header, verbatim and RLE planar storage,
offset/length tables, 8- and 16-bit channels, dimensions 1-3."""
import struct

CMAPS = ("normal", "dithered", "screen", "colormap")
HEADER_SIZE = 512

STORAGE_VERBATIM = 0
STORAGE_RLE = 1


class SgiError(ValueError):
    pass


def _read_rle_row(data, start, width, bpc):
    """Decode one RLE-compressed scanline.

    A packet starts with a single count byte.  Bit 7 clear means the next
    ``count`` pixel values follow literally; bit 7 set means the next pixel
    value is repeated ``count & 0x7f`` times; a count of zero ends the row.
    Pixel values are one byte when bpc is 1, big-endian shorts when bpc is
    2.  A truncated row returns the pixels decoded so far (possibly an empty
    list); the caller decides whether the row is complete.
    """
    out = []
    p = start
    end = len(data)
    while len(out) < width:
        if p >= end:
            return out
        count = data[p]
        p += 1
        if count == 0:
            # A standalone zero count ends the row.  Note that 0x80 is a
            # valid run packet (repeat eight), not an end marker.
            break
        n = count & 0x7F
        if count & 0x80:
            if p + bpc > end:
                return out
            if bpc == 1:
                value = data[p]
            else:
                value = struct.unpack_from(">H", data, p)[0]
            p += bpc
            out.extend([value] * n)
        else:
            if p + n * bpc > end:
                return out
            if bpc == 1:
                out.extend(data[p:p + n])
                p += n
            else:
                out.extend(struct.unpack_from(">%dH" % n, data, p))
                p += n * bpc
    return out[:width]


def parse(data):
    if len(data) < HEADER_SIZE or struct.unpack_from(">h", data, 0)[0] != 474:
        raise SgiError("not an SGI image")
    storage, bpc, dim, width, height, channels = struct.unpack_from(">BBHHHH", data, 2)
    pixmin, pixmax = struct.unpack_from(">ii", data, 12)
    cmap, = struct.unpack_from(">i", data, 104)
    if storage not in (STORAGE_VERBATIM, STORAGE_RLE):
        raise SgiError("unsupported storage %d" % storage)
    if bpc not in (1, 2):
        raise SgiError("unsupported bytes per channel %d" % bpc)
    if dim not in (1, 2, 3):
        raise SgiError("unsupported dimension %d" % dim)

    # Per the spec a dimension-1 image is one row, a dimension-2 image one
    # channel; the unused header size fields are ignored.
    if dim == 1:
        height = 1
        channels = 1
    elif dim == 2:
        channels = 1

    # Rows are keyed by (display row y from the top, channel).  A value of
    # None means the row could not be decoded at all from a truncated file.
    rows = {}
    shared_rows = 0

    if storage == STORAGE_VERBATIM:
        # Planar layout: whole channel planes one after another, each plane
        # holding its rows in bottom-to-top order.
        for c in range(channels):
            plane = HEADER_SIZE + c * height * width * bpc
            for y in range(height):
                src_y = height - 1 - y
                start = plane + src_y * width * bpc
                available = (len(data) - start) // bpc
                if available <= 0:
                    rows[(y, c)] = None
                elif bpc == 1:
                    count = min(width, available)
                    rows[(y, c)] = list(data[start:start + count])
                else:
                    count = min(width, available)
                    rows[(y, c)] = list(
                        struct.unpack_from(">%dH" % count, data, start))
    else:
        n_entries = height * channels
        table_end = HEADER_SIZE + 2 * 4 * n_entries
        if len(data) < table_end:
            # The offset/length tables themselves are cut short.
            offsets = ()
        else:
            offsets = struct.unpack_from(">%dI" % n_entries, data, HEADER_SIZE)
            seen = set()
            for offset in offsets:
                if offset in seen:
                    shared_rows += 1
                else:
                    seen.add(offset)
        # Entries are ordered y*channels+c, i.e. channel changes fastest.
        for c in range(channels):
            for y in range(height):
                index = y * channels + c
                if index >= len(offsets):
                    rows[(y, c)] = None
                    continue
                row = _read_rle_row(data, offsets[index], width, bpc)
                rows[(y, c)] = row if len(row) == width else (row or None)

    complete = all(
        row is not None and len(row) == width for row in rows.values()
    )

    def point(x, y):
        values = []
        for c in range(channels):
            row = rows[(y, c)]
            if row is None or x >= len(row):
                return None
            values.append(row[x])
        return values

    name = data[24:104].split(b"\0", 1)[0].decode("latin-1") or None

    return {
        "storage": "rle" if storage == STORAGE_RLE else "verbatim",
        "bytes_per_channel": bpc,
        "dimension": dim,
        "width": width,
        "height": height,
        "channels": channels,
        "name": name,
        "pixel_range": [pixmin, pixmax],
        "colormap": CMAPS[cmap] if 0 <= cmap < len(CMAPS) else "type_%d" % cmap,
        "shared_rows": shared_rows,
        "complete": complete,
        "points": {
            "top_left": point(0, 0),
            "top_right": point(width - 1, 0),
            "bottom_left": point(0, height - 1),
            "center": point(width // 2, height // 2),
        },
        "max_value": max(
            (value for row in rows.values() if row for value in row),
            default=None,
        ),
    }
