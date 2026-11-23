"""Autodesk FLIC animations (.fli / .flc): header, prefix, frame chunks, palettes, timing."""
import struct
from fractions import Fraction

# 0xAF11 is the original Animator FLI, 0xAF12 the Animator Pro FLC.
FORMATS = {0xAF11: "FLI", 0xAF12: "FLC"}
FRAME, PREFIX, SEGMENT_TABLE, HUFFMAN = 0xF1FA, 0xF100, 0xF1FB, 0xF1FC
# FLIC frame sub-chunk types per the Autodesk FLIC specification.
CHUNKS = {4: "COLOR_256", 7: "DELTA_FLC", 11: "COLOR_64", 12: "DELTA_FLI", 13: "BLACK",
          15: "BYTE_RUN", 16: "FLI_COPY", 18: "PSTAMP", 31: "LABEL"}

# FLI timing is counted in jiffies of 1/70 s; FLC timing is in milliseconds.
FLI_TICKS_PER_SECOND = 70
HEADER_SIZE = 128
PALETTE_TYPES = (4, 11)
KEY_TYPES = (13, 15, 16)  # BLACK, BYTE_RUN, FLI_COPY


class FlicError(ValueError):
    pass


def _u(fmt, data, pos):
    try:
        return struct.unpack_from("<" + fmt, data, pos)
    except struct.error:
        raise FlicError("file ends inside a chunk at %d" % pos)


def _widen(six_bit, value):
    # Widen a 6-bit palette component to 8 bits by repeating its top bits.
    return (value << 2 | value >> 4) if six_bit else value


def _palette(data, b, e, six_bit, pal):
    packets, = _u("H", data, b)
    p, idx = b + 2, 0
    for _ in range(packets):
        skip, count = data[p], data[p + 1]
        p += 2
        idx += skip
        if count == 0:
            count = 256  # a count byte of 0 means a full 256-color packet
        for _ in range(count):
            r, g, bl = data[p:p + 3]
            if idx < 256:
                pal[idx] = "#%02x%02x%02x" % (_widen(six_bit, r),
                                              _widen(six_bit, g),
                                              _widen(six_bit, bl))
            idx += 1
            p += 3
        if p > e:
            raise FlicError("palette chunk overruns its size")


def parse(data):
    if len(data) < HEADER_SIZE:
        raise FlicError("not a FLIC file")
    size, magic, frames, width, height, depth, flags = _u("IHHHHHH", data, 0)
    if magic not in FORMATS:
        raise FlicError("not a FLIC file")
    fmt = FORMATS[magic]
    if fmt == "FLI":
        # FLI header speed is a 16-bit number of 1/70 s jiffies.
        default_delay = Fraction(_u("H", data, 16)[0] * 1000, FLI_TICKS_PER_SECOND)
    else:
        # FLC header speed is a 32-bit delay in milliseconds.
        default_delay = Fraction(_u("I", data, 16)[0])
    # Pixel aspect ratio lives at offsets 40/42 of the 128-byte header.
    dx, dy = _u("HH", data, 40)

    counts, pal, prefix = {}, {}, False
    frame_records = []
    pos = HEADER_SIZE
    # Only the bytes up to the file size given in the header belong to the animation.
    while pos + 6 <= size:
        csize, ctype = _u("IH", data, pos)
        if csize < 6 or pos + csize > size:
            raise FlicError("bad chunk size at %d" % pos)
        if ctype == PREFIX:
            prefix = True
        elif ctype == FRAME:
            n, delay = _u("HH", data, pos + 6)
            if delay:
                # A frame with its own delay uses it; units follow the file format.
                frame_delay = (Fraction(delay * 1000, FLI_TICKS_PER_SECOND) if fmt == "FLI"
                               else Fraction(delay))
            else:
                frame_delay = default_delay
            kinds = []
            p = pos + 16
            for _ in range(n):
                ssize, stype = _u("IH", data, p)
                if ssize < 6 or p + ssize > pos + csize:
                    raise FlicError("bad sub-chunk at %d" % p)
                name = CHUNKS.get(stype, "type_%d" % stype)
                counts[name] = counts.get(name, 0) + 1
                kinds.append(stype)
                # The palette after the first frame supplies first_colors.
                if stype in PALETTE_TYPES and not frame_records:
                    _palette(data, p + 6, p + ssize, stype == 11, pal)
                p += ssize
            frame_records.append((n, kinds, frame_delay))
        pos += csize

    seen = len(frame_records)
    ring_frame = seen == frames + 1
    # The ring frame (last frame chunk, delta back to the first) is not an animation frame.
    animation_frames = frame_records[:frames]
    empty = sum(1 for n, _kinds, _delay in animation_frames if n == 0)
    palette_frames = sum(any(k in PALETTE_TYPES for k in kinds)
                         for _n, kinds, _d in animation_frames)
    key_frames = sum(any(k in KEY_TYPES for k in kinds)
                     for _n, kinds, _d in animation_frames)
    duration = sum((d for _n, _kinds, d in animation_frames), Fraction(0)) / 1000
    return {"format": fmt, "width": width, "height": height, "depth": depth,
            "frames": frames, "ring_frame": ring_frame,
            "frame_delay_ms": str(default_delay),
            "duration": str(duration), "empty_frames": empty, "prefix": prefix,
            "chunks": dict(sorted(counts.items())),
            "first_colors": [pal.get(i) for i in range(4)],
            "pixel_aspect": str(Fraction(dx, dy)) if fmt == "FLC" and dx and dy else None,
            "palette_frames": palette_frames, "key_frames": key_frames}
