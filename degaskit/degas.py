"""Atari ST Degas / Degas Elite pictures (.pi1-.pi3, .pc1-.pc3): resolution word, STE palette, bitplanes, PackBits."""
import zlib

MODES = {0: (320, 200, 4), 1: (640, 200, 2), 2: (640, 400, 1)}


class DegasError(ValueError):
    pass


def ste(v):
    """One STE palette word to 8-bit R, G, B.

    STE colours are 12-bit, four bits per channel: red in bits 11-8, green in
    7-4 and blue in 3-0.  Each channel is expanded 0..15 -> 0..255, so a fully
    set channel is white rather than the 224 that a 3-bit ST palette word gives.
    """
    return tuple(((v >> sh) & 0xF) * 17 for sh in (8, 4, 0))


def unpack(data, pos, size):
    """PackBits the picture data, producing `size` bytes; returns (bytes, new position).

    Degas Elite uses the full 0x81..0xFF range for repeats and counts a run as
    257 - n bytes.  Using 256 - n (plain PackBits) drops one byte from every
    run, which shifts everything after it left and eventually overruns the
    scanline.  A run may span scanlines, as it does in the original format.
    """
    out = bytearray()
    while len(out) < size:
        if pos >= len(data):
            raise DegasError("compressed data ends inside the picture")
        n = data[pos]
        pos += 1
        if n < 0x80:                      # literal run of n + 1 bytes
            cnt = n + 1
            if pos + cnt > len(data):
                raise DegasError("literal run is truncated")
            end = len(out) + cnt
            out += data[pos:pos + cnt]
            pos += cnt
        elif n == 0x80:                   # no operation
            continue
        else:                             # repeat the next byte 257 - n times
            if pos >= len(data):
                raise DegasError("repeat run is truncated")
            end = len(out) + (257 - n)
            out += bytes([data[pos]]) * (257 - n)
            pos += 1
        if end > size:
            raise DegasError("run crosses the end of the picture data")
    return bytes(out), pos


def parse(data):
    if len(data) < 34:
        raise DegasError("header is truncated")
    word = data[0] << 8 | data[1]
    packed = bool(word & 0x8000)
    res = word & 0x7FFF
    if res not in MODES:
        raise DegasError("unknown resolution %d" % res)
    w, h, planes = MODES[res]
    pal = [data[2 + 2 * k] << 8 | data[3 + 2 * k] for k in range(16)]
    bpl = w // 8
    line_size = bpl * planes
    pos = 34
    if packed:
        body, pos = unpack(data, pos, line_size * h)
    else:
        need = line_size * h
        if pos + need > len(data):
            raise DegasError("picture data is truncated")
        body = data[pos:pos + need]
        pos += need
    anim = None
    if packed and pos + 32 <= len(data):
        t = [data[pos + 2 * k] << 8 | data[pos + 2 * k + 1] for k in range(16)]
        anim = [{"first": t[k], "last": t[4 + k], "direction": t[8 + k], "delay": t[12 + k]} for k in range(4)]
    if planes == 1:
        colours = [(255, 255, 255), (0, 0, 0)]
    else:
        colours = [ste(v) for v in pal]
    img = bytearray()
    for y in range(h):
        raw = body[y * line_size:(y + 1) * line_size]
        # The ST screen is planar along each scanline: every bit of plane 0 for
        # the line, then plane 1, and so on.  Interleaving 16-pixel groups of
        # the planes scrambles the picture.
        planes_row = [raw[p * bpl:(p + 1) * bpl] for p in range(planes)]
        for x in range(w):
            idx = 0
            for p in range(planes):
                idx |= ((planes_row[p][x >> 3] >> (7 - (x & 7))) & 1) << p
            img += bytes(colours[idx])
    stride = 3 * w
    return {"width": w, "height": h, "planes": planes, "compressed": packed,
            "palette": ["%03x" % (v & 0xFFF) for v in pal[:1 << planes]] if planes > 1 else None,
            "crc32": "%08x" % zlib.crc32(bytes(img)),
            "first_row": "%08x" % zlib.crc32(bytes(img[:stride])),
            "last_row": "%08x" % zlib.crc32(bytes(img[-stride:])),
            "animation": anim}
