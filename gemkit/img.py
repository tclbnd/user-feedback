"""GEM bit images (.img): header, then run-length encoded scan lines."""
import struct


class ImgError(ValueError):
    pass


def parse(data):
    if len(data) < 16:
        raise ImgError("not a GEM image")
    version, words, planes, patlen, pw, ph, width, height = struct.unpack_from(">8H", data)
    if words < 8 or planes < 1 or planes > 8 or width == 0:
        raise ImgError("not a GEM image")
    if planes != 1:
        raise ImgError("only monochrome images are supported")
    pos = 16
    lb = (width + 7) // 8
    rows = []
    while len(rows) < height and pos < len(data):
        buf = bytearray()
        while len(buf) < lb and pos < len(data):
            b = data[pos]
            if b == 0:
                n = data[pos + 1]
                buf += data[pos + 2:pos + 4] * n
                pos += 4
            elif b == 0x80:
                n = data[pos + 1]
                buf += data[pos + 2:pos + 2 + n]
                pos += 2 + n
            else:
                buf += (b"\xff" if b & 0x80 else b"\x00") * (b & 0x7F)
                pos += 1
        if len(buf) < lb:
            break
        rows.append([bytes(buf[:lb])])
    hist = [0, 0]
    first = None
    for y, line in enumerate(rows):
        idx = [line[0][x >> 3] >> (7 - (x & 7)) & 1 for x in range(width)]
        for v in idx:
            hist[v] += 1
        if y == 0:
            first = "".join("%x" % v for v in idx)
    return {"width": width, "height": height, "planes": planes, "rows": len(rows), "complete": len(rows) == height,
            "palette": None, "histogram": hist, "first_row": first}
