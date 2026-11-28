"""Silicon Graphics RLE video (8-bit RBG323): runs and raw spans that wrap across lines."""
import struct
import zlib


class SgrError(ValueError):
    pass


def bgr8(x):
    """Repack an RBG323 pixel as BGR8 (2 bits blue, 3 bits green, 3 bits red)."""
    return ((x & 0x18) << 3) | ((x & 0x07) << 3) | ((x >> 5) & 0x07)


def frame(src, w, h):
    total = w * h
    pix = bytearray(total)
    pos = 0
    p, end = 0, len(src)
    while p < end and pos < total:
        v = src[p]
        p += 1
        if v == 0:
            continue
        if v < 0xC0:
            # Opcodes below 0xC0 are runs of one repeated pixel; a run
            # keeps going across scanlines.
            if p >= end:
                raise SgrError("run data is truncated")
            c = bgr8(src[p])
            p += 1
            n = min(v, total - pos)
            pix[pos:pos + n] = bytes([c]) * n
            pos += v
        else:
            # 0xC0..0xFF start a span of raw pixels; the count is the
            # opcode minus 0xC0, so 0xC0 is an empty (no-op) span.
            n = v - 0xC0
            if end - p < n:
                raise SgrError("raw data is truncated")
            k = min(n, total - pos)
            pix[pos:pos + k] = bytes(bgr8(b) for b in src[p:p + k])
            pos += n
            p += n
    return pix


def parse(data):
    if data[:8] != b"SGRLPACK":
        raise SgrError("not an SGI RLE pack")
    w, h, count = struct.unpack_from("<HHH", data, 8)
    if not w or not h:
        raise SgrError("empty picture")
    pos = 14
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise SgrError("frame table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        buf = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(buf) < n:
            raise SgrError("frame is truncated")
        try:
            pix = frame(buf, w, h)
        except SgrError:
            frames.append({"state": "error", "crc32": None})
            continue
        frames.append({"state": "decoded", "crc32": "%08x" % zlib.crc32(bytes(pix)), "colours": len(set(pix))})
    return {"width": w, "height": h, "frames": frames}
