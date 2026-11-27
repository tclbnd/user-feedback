"""QOI image decoder."""
import zlib

MAGIC = b"qoif"
PADDING = 8


class QoiError(Exception):
    pass


def parse(data):
    if len(data) < 14 + PADDING or data[:4] != MAGIC:
        raise QoiError("not a QOI image")
    width = int.from_bytes(data[4:8], "big")
    height = int.from_bytes(data[8:12], "big")
    channels, colorspace = data[12], data[13]
    if channels not in (3, 4):
        raise QoiError("bad channel count")
    end = len(data) - PADDING
    index = [(0, 0, 0, 0)] * 64
    r, g, b, a = 0, 0, 0, 0
    run, p = 0, 14
    ops = {"rgb": 0, "rgba": 0, "index": 0, "diff": 0, "luma": 0, "run": 0}
    out = bytearray()
    for _ in range(width * height):
        if run > 0:
            run -= 1
        else:
            if p >= end:
                raise QoiError("image data ends early")
            b1 = data[p]
            p += 1
            if b1 >> 6 == 0:
                r, g, b, a = index[b1]
                ops["index"] += 1
            elif b1 >> 6 == 1:
                r = r + ((b1 >> 4) & 3) - 2
                g = g + ((b1 >> 2) & 3) - 2
                b = b + (b1 & 3) - 2
                ops["diff"] += 1
            elif b1 >> 6 == 2:
                b2 = data[p]
                p += 1
                vg = (b1 & 0x3F) - 32
                r = (r + vg + ((b2 >> 4) & 0x0F)) & 0xFF
                g = (g + vg) & 0xFF
                b = (b + vg + (b2 & 0x0F)) & 0xFF
                ops["luma"] += 1
            elif b1 >> 6 == 3:
                run = (b1 & 0x3F) - 1
                ops["run"] += 1
            elif b1 == 0xFE:
                r, g, b, a = data[p], data[p + 1], data[p + 2], 255
                p += 3
                ops["rgb"] += 1
            else:
                r, g, b, a = data[p], data[p + 1], data[p + 2], data[p + 3]
                p += 4
                ops["rgba"] += 1
            if b1 >> 6 != 3:
                index[(r * 3 + g * 5 + b * 7 + a * 13) % 64] = (r, g, b, a)
        out += bytes((r, g, b, a))
    row = width * 4
    return {"width": width, "height": height, "channels": channels, "colorspace": "linear" if colorspace else "srgb",
            "ops": ops, "pixels": width * height, "crc32": "%08x" % zlib.crc32(bytes(out)),
            "first_row": "%08x" % zlib.crc32(bytes(out[:row])), "last_row": "%08x" % zlib.crc32(bytes(out[-row:]))}
