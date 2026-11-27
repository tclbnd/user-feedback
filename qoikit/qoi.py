"""QOI 1.0 image decoder.

Implements https://qoiformat.org/qoi-spec.pdf exactly: 14-byte header,
the six chunk tags (QOI_OP_INDEX/DIFF/LUMA/RUN/RGB/RGBA), a 64-entry
running colour index and the 8-byte end marker.
"""
import zlib

MAGIC = b"qoif"
HEADER_SIZE = 14
PADDING = 8
END_MARKER = b"\x00\x00\x00\x00\x00\x00\x00\x01"
INDEX_SIZE = 64
# Spec limit: at most 400 million pixels.
MAX_PIXELS = 400_000_000


class QoiError(Exception):
    pass


def _hash_position(r, g, b, a):
    return (r * 3 + g * 5 + b * 7 + a * 11) % INDEX_SIZE


def parse(data):
    if len(data) < HEADER_SIZE + PADDING or data[:4] != MAGIC:
        raise QoiError("not a QOI image")
    width = int.from_bytes(data[4:8], "big")
    height = int.from_bytes(data[8:12], "big")
    channels = data[12]
    colorspace = data[13]
    if channels not in (3, 4):
        raise QoiError("bad channel count")
    if colorspace not in (0, 1):
        raise QoiError("bad colorspace")
    if width == 0 or height == 0:
        raise QoiError("image dimensions must be non-zero")
    if width > MAX_PIXELS or height > MAX_PIXELS or width * height > MAX_PIXELS:
        raise QoiError("image too large")

    end = len(data) - PADDING
    index = [(0, 0, 0, 0)] * INDEX_SIZE
    r = g = b = 0
    a = 255
    run = 0
    p = HEADER_SIZE
    total = width * height
    ops = {"rgb": 0, "rgba": 0, "index": 0, "diff": 0, "luma": 0, "run": 0}
    out = bytearray()

    for _ in range(total):
        if run > 0:
            run -= 1
        else:
            if p >= end:
                raise QoiError("image data ends early")
            b1 = data[p]
            p += 1

            if b1 == 0xFE:  # QOI_OP_RGB: R,G,B follow; alpha stays unchanged.
                if p + 3 > end:
                    raise QoiError("truncated rgb chunk")
                r, g, b = data[p], data[p + 1], data[p + 2]
                p += 3
                ops["rgb"] += 1
                stored = True
            elif b1 == 0xFF:  # QOI_OP_RGBA: R,G,B,A follow.
                if p + 4 > end:
                    raise QoiError("truncated rgba chunk")
                r, g, b, a = data[p], data[p + 1], data[p + 2], data[p + 3]
                p += 4
                ops["rgba"] += 1
                stored = True
            elif b1 >> 6 == 0:  # QOI_OP_INDEX
                r, g, b, a = index[b1]
                ops["index"] += 1
                stored = True
            elif b1 >> 6 == 1:  # QOI_OP_DIFF: signed 2-bit deltas, wrap mod 256.
                r = (r + ((b1 >> 4) & 0x03) - 2) & 0xFF
                g = (g + ((b1 >> 2) & 0x03) - 2) & 0xFF
                b = (b + (b1 & 0x03) - 2) & 0xFF
                ops["diff"] += 1
                stored = True
            elif b1 >> 6 == 2:  # QOI_OP_LUMA
                if p >= end:
                    raise QoiError("truncated luma chunk")
                b2 = data[p]
                p += 1
                vg = (b1 & 0x3F) - 32
                dr_dg = ((b2 >> 4) & 0x0F) - 8
                db_dg = (b2 & 0x0F) - 8
                r = (r + vg + dr_dg) & 0xFF
                g = (g + vg) & 0xFF
                b = (b + vg + db_dg) & 0xFF
                ops["luma"] += 1
                stored = True
            else:  # b1 >> 6 == 3: QOI_OP_RUN; bias 1, values 62/63 illegal.
                length = b1 & 0x3F
                if length > 61:
                    raise QoiError("illegal run length %d" % length)
                run = length
                ops["run"] += 1
                stored = False

            if stored:
                index[_hash_position(r, g, b, a)] = (r, g, b, a)

        out.extend((r, g, b) if channels == 3 else (r, g, b, a))

    if run != 0:
        raise QoiError("run extends past the last pixel")
    if p != end:
        raise QoiError("unexpected bytes before the end marker")
    if data[end:] != END_MARKER:
        raise QoiError("missing 0x0000000000000001 end marker")

    row = width * channels
    return {
        "width": width,
        "height": height,
        "channels": channels,
        "colorspace": "linear" if colorspace else "srgb",
        "ops": ops,
        "pixels": total,
        "crc32": "%08x" % zlib.crc32(bytes(out)),
        "first_row": "%08x" % zlib.crc32(bytes(out[:row])),
        "last_row": "%08x" % zlib.crc32(bytes(out[-row:])),
    }
