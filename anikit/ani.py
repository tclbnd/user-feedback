"""Windows animated cursors (.ani): RIFF ACON, anih header, icon/cursor frames."""
import struct
from fractions import Fraction

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class AniError(ValueError):
    pass


def chunks(data, p, end):
    """Yield ``(id, payload_start, payload_end)`` for RIFF chunks.

    RIFF chunk bodies are word aligned, so an odd body length is followed by
    one padding byte.  The padding byte is not part of the yielded payload.
    """
    while p + 8 <= end:
        cid, size = data[p:p + 4], struct.unpack_from("<I", data, p + 4)[0]
        body = p + 8
        payload_end = min(body + size, end)
        yield cid, body, payload_end
        p = body + size + (size & 1)


def _zstring(raw):
    """Decode a NUL-terminated RIFF INFO string."""
    raw = raw.split(b"\0", 1)[0]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _is_png(d, offset):
    return 0 <= offset and offset + len(PNG_SIGNATURE) <= len(d) and \
        d[offset:offset + len(PNG_SIGNATURE)] == PNG_SIGNATURE


def _bitmap_bit_count(d, offset):
    """Read biBitCount from a BITMAPINFOHEADER/BITMAPCOREHEADER."""
    if offset < 0 or offset + 12 > len(d):
        return None
    header_size = struct.unpack_from("<I", d, offset)[0]
    if header_size == 12 and offset + 12 <= len(d):
        return struct.unpack_from("<H", d, offset + 10)[0] or None
    if header_size >= 40 and offset + 16 <= len(d):
        return struct.unpack_from("<H", d, offset + 14)[0] or None
    return None


def _png_bit_count(d, offset):
    """Return bits per pixel represented by a PNG IHDR."""
    # Signature (8), chunk length/type (8), then IHDR fields.
    if offset < 0 or offset + 26 > len(d) or d[offset + 12:offset + 16] != b"IHDR":
        return None
    bit_depth = d[offset + 24]
    colour_type = d[offset + 25]
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colour_type)
    if channels is None:
        return None
    return bit_depth * channels


def icon_frame(d):
    """The first image of an ICO/CUR resource."""
    if len(d) < 6:
        return None
    _, kind, count = struct.unpack_from("<HHH", d, 0)
    if len(d) < 22:
        return None

    w, h, _, _, word_a, word_b, _, image_offset = struct.unpack_from(
        "<BBBBHHII", d, 6
    )
    width = 256 if w == 0 else w
    height = 256 if h == 0 else h
    png = _is_png(d, image_offset)

    if kind == 2:  # CUR: the two directory words are the hotspot.
        frame_type = "cursor"
        hotspot = [word_a, word_b]
        if png:
            bit_count = _png_bit_count(d, image_offset) or word_b
        else:
            bit_count = _bitmap_bit_count(d, image_offset) or word_b
    else:
        frame_type = "icon"
        hotspot = None
        # ICO directory entries carry planes and bits per pixel.
        bit_count = word_b

    return {
        "type": frame_type,
        "width": width,
        "height": height,
        "bit_count": bit_count,
        "png": png,
        "images": count,
        "hotspot": hotspot,
    }


def _read_dwords(data, b, e):
    count = (e - b) // 4
    return [struct.unpack_from("<I", data, b + 4 * i)[0] for i in range(count)]


def _read_info(data, b, e):
    title = artist = None
    for cid, value_start, value_end in chunks(data, b, e):
        value = _zstring(data[value_start:value_end])
        if cid == b"INAM":
            title = value
        elif cid == b"IART":
            artist = value
    return title, artist


def parse(data):
    if data[:4] != b"RIFF" or data[8:12] != b"ACON":
        raise AniError("not an animated cursor")

    riff_size = struct.unpack_from("<I", data, 4)[0]
    end = min(len(data), 8 + riff_size)
    head = None
    sequence = None
    step_rates = None
    title = artist = None
    frames = []

    for cid, b, e in chunks(data, 12, end):
        if cid == b"anih":
            head = struct.unpack_from("<9I", data, b)
        elif cid == b"rate":
            step_rates = _read_dwords(data, b, e)
        elif cid == b"seq ":
            sequence = _read_dwords(data, b, e)
        elif cid == b"LIST":
            list_type = data[b:b + 4]
            if list_type == b"INFO":
                title, artist = _read_info(data, b + 4, e)
            elif list_type == b"fram":
                for c2, b2, e2 in chunks(data, b + 4, e):
                    if c2 == b"icon":
                        frames.append(icon_frame(data[b2:e2]))
        elif cid == b"INAM":
            title = _zstring(data[b:e])
        elif cid == b"IART":
            artist = _zstring(data[b:e])

    if head is None:
        raise AniError("no anih chunk")

    _, nframes, nsteps, _, _, _bits, _, display_rate, attrs = head
    if sequence is None:
        sequence = list(range(nsteps))

    if step_rates is None:
        total_rate = display_rate * len(sequence)
    else:
        total_rate = sum(step_rates)

    return {
        "frames": nframes,
        "steps": len(sequence),
        "sequence": sequence,
        "default_rate": display_rate,
        "loop_seconds": str(Fraction(total_rate, 60)),
        "icon_frames": bool(attrs & 1),
        "title": title,
        "artist": artist,
        "frame_images": frames,
    }
