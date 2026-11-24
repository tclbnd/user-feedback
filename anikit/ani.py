"""Windows animated cursors (.ani): RIFF ACON, anih header, ICO/CUR frames."""
import struct
from fractions import Fraction

# ANI display rates are measured in jiffies: 1/60 of a second.
JIFFIES_PER_SECOND = 60
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Channels per pixel for the PNG IHDR colour types.
_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class AniError(ValueError):
    pass


def chunks(data, p, end):
    """Yield (chunk_id, body_start, body_end) for RIFF chunks.

    RIFF chunks are word aligned: a chunk with an odd body size is followed
    by a single padding byte, which must be skipped to find the next chunk.
    """
    while p + 8 <= end:
        cid = data[p:p + 4]
        size = struct.unpack_from("<I", data, p + 4)[0]
        body = p + 8
        body_end = body + size
        if body_end > end:
            yield cid, body, end
            return
        yield cid, body, body_end
        p = body_end + (size & 1)


def _uint32_list(raw):
    count = len(raw) // 4
    return list(struct.unpack("<%dI" % count, raw[:count * 4])) if count else []


def _text(raw):
    """Decode a null-terminated INFO list string."""
    value = raw.rstrip(b"\x00").decode("utf-8", "replace")
    return value or None


def _png_bit_count(d, off):
    """Bits per pixel derived from the IHDR of a PNG compressed image."""
    # signature(8) + IHDR length(4) + "IHDR"(4) + width(4) + height(4)
    if off + 26 > len(d):
        return 0
    bit_depth, color_type = d[off + 24], d[off + 25]
    return bit_depth * _PNG_CHANNELS.get(color_type, 1)


def _image_bit_count(d, off, is_png):
    if is_png:
        return _png_bit_count(d, off)
    # BITMAPINFOHEADER: ..., biPlanes (u16 at +12), biBitCount (u16 at +14).
    if off + 16 <= len(d):
        return struct.unpack_from("<H", d, off + 14)[0]
    return 0


def icon_frame(d):
    """Parse one ICO (type 1) or CUR (type 2) resource body.

    The resource header is followed by 16-byte directory entries; the first
    entry describes the representative image reported here, while ``images``
    counts every entry in the resource.
    """
    if len(d) < 6:
        raise AniError("truncated icon frame")
    _reserved, kind, count = struct.unpack_from("<HHH", d, 0)
    if count == 0 or len(d) < 6 + 16 * count:
        raise AniError("truncated icon directory")
    width0, height0, _colors, _rsv, field1, field2, _img_size, img_off = \
        struct.unpack_from("<BBBBHHII", d, 6)
    is_cursor = kind == 2
    is_png = len(d) >= img_off + 8 and d[img_off:img_off + 8] == PNG_SIGNATURE
    if is_cursor:
        # For cursors the entry union holds the hotspot, not planes/bpp.
        hotspot = [field1, field2]
        bit_count = _image_bit_count(d, img_off, is_png)
    else:
        hotspot = None
        bit_count = field2 or _image_bit_count(d, img_off, is_png)
    return {"type": "cursor" if is_cursor else "icon",
            # A zero dimension in the directory means 256 pixels.
            "width": width0 or 256,
            "height": height0 or 256,
            "bit_count": bit_count,
            "png": is_png,
            "images": count,
            "hotspot": hotspot}


def parse(data):
    if data[:4] != b"RIFF" or data[8:12] != b"ACON":
        raise AniError("not an animated cursor")
    end = min(len(data), 8 + struct.unpack_from("<I", data, 4)[0])

    head = None
    rates = []
    raw_sequence = []
    title = artist = None
    frames = []

    for cid, b, e in chunks(data, 12, end):
        if cid == b"anih":
            if e - b < 36:
                raise AniError("truncated anih chunk")
            head = struct.unpack_from("<9I", data, b)
        elif cid == b"rate":
            rates = _uint32_list(data[b:e])
        elif cid == b"seq ":
            raw_sequence = _uint32_list(data[b:e])
        elif cid == b"LIST" and e - b >= 4:
            list_type = data[b:b + 4]
            if list_type == b"INFO":
                for sub, sb, se in chunks(data, b + 4, e):
                    if sub == b"INAM":
                        title = _text(data[sb:se])
                    elif sub == b"IART":
                        artist = _text(data[sb:se])
            elif list_type == b"fram":
                for sub, sb, se in chunks(data, b + 4, e):
                    if sub == b"icon":
                        frames.append(icon_frame(data[sb:se]))

    if head is None:
        raise AniError("no anih chunk")
    _cb, nframes, nsteps, _w, _h, _bits, _planes, default_rate, attrs = head

    # Without a seq chunk the steps simply play the frames in order.
    if raw_sequence:
        sequence = [raw_sequence[i] if i < len(raw_sequence) else i
                    for i in range(nsteps)]
    else:
        sequence = list(range(nsteps))

    # Each step uses its rate chunk entry when present, else the header rate.
    total_jiffies = sum(rates[i] if i < len(rates) else default_rate
                        for i in range(nsteps))

    return {"frames": nframes, "steps": nsteps, "sequence": sequence,
            "default_rate": default_rate,
            "loop_seconds": str(Fraction(total_jiffies, JIFFIES_PER_SECOND)),
            "icon_frames": bool(attrs & 1),
            "title": title, "artist": artist,
            "frame_images": frames}
