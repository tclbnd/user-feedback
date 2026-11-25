"""GEM bit images (.img): header, then run-length encoded scan lines."""
import struct


class ImgError(ValueError):
    pass


# version, header length in words, bit planes, pattern length,
# pixel width/height, image width/height in pixels
HEADER = struct.Struct(">8H")


def _palette(data, planes, words):
    """Return the XIMG palette, or None if absent or not inline."""
    header_end = min(words * 2, len(data))
    # The extension may follow other header extensions, so scan the header.
    at = data.find(b"XIMG", 16, header_end)
    if at < 0 or at + 6 > header_end:
        return None
    # Bit 0 of the flag word: set means the standard palette is used and no
    # RGB values follow.
    if struct.unpack_from(">H", data, at + 4)[0] & 1:
        return None
    count = 1 << planes
    start = at + 6
    if start + count * 6 > header_end:
        return None
    out = []
    for i in range(count):
        rgb = struct.unpack_from(">3H", data, start + i * 6)
        values = [min(255, max(0, round(c * 255 / 1000))) for c in rgb]
        out.append("#" + "".join("%02x" % v for v in values))
    return out


def _decode_rows(data, pos, rowbytes, height, patlen):
    """Decode compressed scan lines starting at *pos*.

    Each record begins with a control byte:

    ``00 nn`` then *patlen* pattern bytes
        repeat the pattern *nn* times; when ``nn`` is zero the previous scan
        line is repeated and the pattern bytes are padding.
    ``80 nn`` then *nn* bytes
        copy the literal bytes.
    ``01``..``7f``
        repeat a zero byte that many times.
    ``81``..``ff``
        repeat an ``ff`` byte that many times.

    A scan line is *rowbytes* bytes holding every bit plane of the line,
    plane 0 first, and is kept only when complete.
    """
    rows = []
    pending = bytearray()
    n = len(data)
    while len(rows) < height and pos < n:
        b = data[pos]
        if b == 0:
            if pos + 2 > n:
                break
            count = data[pos + 1]
            pos += 2
            if pos + patlen > n:
                break
            pattern = data[pos:pos + patlen]
            pos += patlen
            if count == 0:
                # Vertical repeat: the previous scan line is repeated and the
                # pattern bytes are padding.  Drop any partial line first.
                if not rows:
                    break
                pending.clear()
                rows.append(rows[-1])
                continue
            chunk = pattern * count
        elif b == 0x80:
            if pos + 2 > n:
                break
            count = data[pos + 1]
            pos += 2
            if pos + count > n:
                break
            chunk = data[pos:pos + count]
            pos += count
        elif b & 0x80:
            chunk = b"\xff" * (b & 0x7F)
            pos += 1
        else:
            chunk = b"\x00" * b
            pos += 1

        pending += chunk
        while len(pending) >= rowbytes and len(rows) < height:
            rows.append(bytes(pending[:rowbytes]))
            del pending[:rowbytes]
    return rows


def _row_indices(line, width, planes, plane_bytes):
    """Colour index of each pixel in one scan line; plane 0 is the low bit."""
    for x in range(width):
        byte = x >> 3
        bit = 7 - (x & 7)
        value = 0
        for p in range(planes):
            if line[p * plane_bytes + byte] >> bit & 1:
                value |= 1 << p
        yield value


def parse(data):
    if len(data) < HEADER.size:
        raise ImgError("not a GEM image")
    version, words, planes, patlen, pw, ph, width, height = HEADER.unpack_from(data)
    if words < 8 or planes < 1 or planes > 8 or width == 0:
        raise ImgError("not a GEM image")
    if patlen < 1 or patlen > 8:
        raise ImgError("bad pattern length")

    plane_bytes = (width + 7) // 8
    rowbytes = plane_bytes * planes
    pos = words * 2
    rows = _decode_rows(data, pos, rowbytes, height, patlen) if pos <= len(data) else []

    hist = [0] * (1 << planes)
    first = None
    for y, line in enumerate(rows):
        idx = list(_row_indices(line, width, planes, plane_bytes))
        for v in idx:
            hist[v] += 1
        if y == 0:
            first = "".join("%x" % v for v in idx)

    return {"width": width, "height": height, "planes": planes,
            "rows": len(rows), "complete": len(rows) == height,
            "palette": _palette(data, planes, words),
            "histogram": hist, "first_row": first}
