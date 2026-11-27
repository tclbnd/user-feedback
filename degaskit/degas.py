"""Atari ST Degas / Degas Elite pictures (.pi1-.pi3, .pc1-.pc3): resolution word, STE palette, bitplanes, PackBits."""
import zlib

MODES = {0: (320, 200, 4), 1: (640, 200, 4), 2: (640, 400, 1)}


class DegasError(ValueError):
    pass


def ste(v):
    """One palette word to 8-bit R, G, B."""
    return tuple(((v >> sh) & 7) * 32 for sh in (8, 4, 0))


def unpack_line(data, pos, size):
    """PackBits one scanline of `size` bytes; returns (bytes, new position)."""
    out = bytearray()
    while len(out) < size:
        if pos >= len(data):
            raise DegasError("compressed data ends inside a scanline")
        n = data[pos]
        pos += 1
        if n < 0x80:
            cnt = n + 1
            if pos + cnt > len(data):
                raise DegasError("literal run is truncated")
            out += data[pos:pos + cnt]
            pos += cnt
        elif n == 0x80:
            continue
        else:
            if pos >= len(data):
                raise DegasError("repeat run is truncated")
            out += bytes([data[pos]]) * (256 - n)
            pos += 1
        if len(out) > size:
            raise DegasError("run crosses the end of a scanline")
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
    rows = []
    pos = 34
    if packed:
        for _ in range(h):
            raw, pos = unpack_line(data, pos, bpl * planes)
            rows.append([b"".join(raw[(g * planes + p) * 2:(g * planes + p) * 2 + 2] for g in range(w // 16)) for p in range(planes)])
    else:
        need = bpl * planes * h
        if pos + need > len(data):
            raise DegasError("picture data is truncated")
        for y in range(h):
            raw = data[pos + y * bpl * planes:pos + (y + 1) * bpl * planes]
            rows.append([b"".join(raw[(g * planes + p) * 2:(g * planes + p) * 2 + 2] for g in range(w // 16)) for p in range(planes)])
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
    for planes_row in rows:
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
