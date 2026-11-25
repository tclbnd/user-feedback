"""GEM bit images (.img): header, then run-length encoded scan lines."""
import struct


class ImgError(ValueError):
    pass


def _colour(value):
    """Scale an XIMG palette component (0-1000) to a 0-255 channel value."""
    return max(0, min(255, round(value * 255 / 1000)))


def _read_palette(data, words, planes):
    """Decode the RGB palette stored in an XIMG header extension, if there is one."""
    if words < 11 or len(data) < 22 or data[16:20] != b"XIMG":
        return None
    # The extension is: "XIMG", a flag word (0 = a palette follows), then one
    # red/green/blue triple of words for every possible colour index.  Other
    # header extensions (NOSIG, STTT, TIMG, ...) carry no palette.
    if struct.unpack_from(">H", data, 20)[0] != 0:
        return None
    colours = 1 << planes
    if words * 2 < 22 + 6 * colours or len(data) < 22 + 6 * colours:
        return None
    palette = []
    for i in range(colours):
        r, g, b = struct.unpack_from(">3H", data, 22 + 6 * i)
        palette.append("#%02x%02x%02x" % (_colour(r), _colour(g), _colour(b)))
    return palette


def _read_rows(data, pos, height, line_len, patlen):
    """Decode scan lines; every line holds all planes' bytes, one after the other."""
    rows = []
    previous = None
    while len(rows) < height and pos < len(data):
        buf = bytearray()
        repeated = False
        while len(buf) < line_len and pos < len(data):
            code = data[pos]
            if code == 0:
                # "Pattern" record: a count byte and always `patlen` bytes of
                # pattern, counting as 2 + patlen bytes.  A count of zero at
                # the start of a line means the previous line is repeated
                # (vertical repetition); its pattern bytes are unused but still
                # occupy space in the stream.
                end = pos + 2 + patlen
                if end > len(data):
                    pos = len(data)
                    break
                count = data[pos + 1]
                if count == 0 and not buf and previous is not None:
                    repeated = True
                    pos = end
                    break
                if count:
                    buf += (data[pos + 2:end] * count)[:line_len - len(buf)]
                pos = end
            elif code == 0x80:
                # Copy the following bytes literally.
                if pos + 1 >= len(data):
                    pos = len(data)
                    break
                end = min(pos + 2 + data[pos + 1], len(data))
                buf += data[pos + 2:end][:line_len - len(buf)]
                pos = end
            else:
                # Run of `code & 0x7f` bytes, all 0x00 or all 0xff.
                pos += 1
                buf += (b"\xff" if code & 0x80 else b"\x00") * (code & 0x7F)
        if repeated:
            rows.append(previous)
            continue
        if len(buf) < line_len:
            break
        line = bytes(buf[:line_len])
        rows.append(line)
        previous = line
    return rows


def parse(data):
    if len(data) < 16:
        raise ImgError("not a GEM image")
    version, words, planes, patlen, pw, ph, width, height = struct.unpack_from(">8H", data)
    if words < 8 or planes < 1 or planes > 8 or width == 0:
        raise ImgError("not a GEM image")
    palette = _read_palette(data, words, planes)
    lb = (width + 7) // 8
    rows = _read_rows(data, words * 2, height, lb * planes, patlen)
    hist = [0] * (1 << planes)
    first = None
    for y, line in enumerate(rows):
        for x in range(width):
            byte = x >> 3
            bit = 7 - (x & 7)
            index = 0
            for plane in range(planes):
                index |= (line[plane * lb + byte] >> bit & 1) << plane
            hist[index] += 1
            if y == 0:
                first = (first or "") + "%x" % index
    return {"width": width, "height": height, "planes": planes, "rows": len(rows), "complete": len(rows) == height,
            "palette": palette, "histogram": hist, "first_row": first}
