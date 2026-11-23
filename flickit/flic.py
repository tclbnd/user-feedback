"""Autodesk FLIC animations (.fli / .flc): header, prefix, frame chunks, palettes, timing."""
import struct
from fractions import Fraction

FORMATS = {0xAF11: "FLC", 0xAF12: "FLI"}
FRAME, PREFIX, SEGMENT_TABLE, HUFFMAN = 0xF1FA, 0xF100, 0xF1FB, 0xF1FC
CHUNKS = {4: "COLOR_256", 7: "DELTA_FLI", 11: "COLOR_64", 12: "DELTA_FLC", 13: "BLACK", 15: "BYTE_RUN",
          16: "FLI_COPY", 18: "PSTAMP", 31: "LABEL"}


class FlicError(ValueError):
    pass


def _u(fmt, data, pos):
    try:
        return struct.unpack_from("<" + fmt, data, pos)
    except struct.error:
        raise FlicError("file ends inside a chunk at %d" % pos)


def _palette(data, b, e, six_bit, pal):
    packets, = _u("H", data, b)
    p, idx = b + 2, 0
    for _ in range(packets):
        skip, count = data[p], data[p + 1]
        p += 2
        idx += skip
        for _ in range(count):
            r, g, bl = data[p:p + 3]
            if idx < 256:
                pal[idx] = "#%02x%02x%02x" % (r, g, bl)
            idx += 1
            p += 3
        if p > e:
            raise FlicError("palette chunk overruns its size")


def parse(data):
    if len(data) < 128:
        raise FlicError("not a FLIC file")
    size, magic, frames, width, height, depth, flags = _u("IHHHHHH", data, 0)
    dx, dy = _u("HH", data, 38)
    palette_frames = key_frames = 0
    if magic not in FORMATS:
        raise FlicError("not a FLIC file")
    fmt = FORMATS[magic]
    if fmt == "FLI":
        speed = Fraction(_u("H", data, 16)[0])
    else:
        speed = Fraction(_u("I", data, 16)[0])
    pos = 128
    counts, pal, delays, empty, prefix = {}, {}, [], 0, False
    seen = 0
    while pos + 6 <= len(data):
        csize, ctype = _u("IH", data, pos)
        if csize < 6 or pos + csize > len(data):
            raise FlicError("bad chunk size at %d" % pos)
        if ctype == PREFIX:
            prefix = True
        elif ctype == FRAME:
            n, delay = _u("HH", data, pos + 6)
            seen += 1
            delays.append(speed)
            if n == 0:
                empty += 1
            p = pos + 16
            kinds = []
            for _ in range(n):
                ssize, stype = _u("IH", data, p)
                if ssize < 6 or p + ssize > pos + csize:
                    raise FlicError("bad sub-chunk at %d" % p)
                name = CHUNKS.get(stype, "type_%d" % stype)
                counts[name] = counts.get(name, 0) + 1
                kinds.append(stype)
                if stype in (4, 11) and seen == 1:
                    _palette(data, p + 6, p + ssize, stype == 11, pal)
                p += ssize
            palette_frames += sum(k in (4, 11) for k in kinds)
            key_frames += any(k in (15, 16) for k in kinds)
        pos += csize
    return {"format": fmt, "width": width, "height": height, "depth": depth, "frames": seen,
            "ring_frame": seen == frames + 1, "frame_delay_ms": str(speed),
            "duration": str(sum(delays, Fraction(0)) / 1000), "empty_frames": empty, "prefix": prefix,
            "chunks": dict(sorted(counts.items())),
            "first_colors": [pal.get(i) for i in range(4)],
            "pixel_aspect": "%d/%d" % (dx, dy) if dx and dy else None, "palette_frames": palette_frames, "key_frames": key_frames}
