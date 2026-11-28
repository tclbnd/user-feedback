"""Silicon Graphics RLE video (8-bit RBG323): runs and raw spans that wrap across lines."""
import struct
import zlib


class SgrError(ValueError):
    pass


def bgr8(x):
    """Pixel byte as stored."""
    return x


def frame(src, w, h):
    pix = bytearray(w * h)
    x = y = 0
    p, end = 0, len(src)
    while p < end:
        v = src[p]
        p += 1
        if v == 0 or v == 0xC0:
            continue
        if v < 0x80:
            c = bgr8(src[p] if p < end else 0)
            p += 1
            n = min(v, w - x)
            if y < h:
                pix[y * w + x:y * w + x + n] = bytes([c]) * n
            x += n
        else:
            n = v - 0x80 if v < 0xC0 else v - 0xBF
            if end - p < n:
                raise SgrError("raw data is truncated")
            k = min(n, w - x)
            if y < h:
                pix[y * w + x:y * w + x + k] = bytes(bgr8(b) for b in src[p:p + k])
            x += k
            p += n
        if x >= w:
            x = 0
            y += 1
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
