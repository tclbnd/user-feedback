"""Silicon Graphics Motion Video Compressor frames: MVC1 (RGB555 4x4 blocks)."""
import struct
import zlib


class MvcError(ValueError):
    pass


def be16(b, p):
    return b[p] << 8 | b[p + 1]


def mvc1(buf, w, h, pix):
    p = 0
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            if len(buf) - p < 6:
                raise MvcError("block data is truncated")
            mask = buf[p] | buf[p + 1] << 8
            v = [be16(buf, p + 2), be16(buf, p + 4)]
            p += 6
            if v[1] & 0x8000:
                if len(buf) - p < 12:
                    raise MvcError("block data is truncated")
                v += [be16(buf, p + 2 * i) for i in range(6)]
                p += 12
            else:
                v = [v[0], v[1]] * 4
            for row in range(4):
                a1, a0, b1, b0 = (0, 1, 2, 3) if row < 2 else (4, 5, 6, 7)
                for col in range(4):
                    bit = mask >> (row * 4 + col) & 1
                    i = (a1 if bit else a0) if col < 2 else (b1 if bit else b0)
                    pix[(y + row) * w + x + col] = v[i] & 0x7FFF
    return pix


def parse(data):
    if data[:8] != b"MVCPACK1":
        raise MvcError("not an MVC pack")
    tag = data[8:12]
    w, h, elen = struct.unpack_from("<HHH", data, 12)
    extra = data[18:18 + elen]
    pos = 18 + elen
    count, = struct.unpack_from("<H", data, pos)
    pos += 2
    if tag in (b"MVC1", b"MVC2"):
        w, h = w & ~3, h & ~3
    else:
        raise MvcError("unknown codec")
    if not w or not h:
        raise MvcError("empty picture")
    frames = []
    last = [0] * (w * h)
    for _ in range(count):
        if pos + 4 > len(data):
            raise MvcError("frame table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        buf = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(buf) < n:
            raise MvcError("frame is truncated")
        try:
            if tag != b"MVC1":
                raise MvcError("MVC2 frames are not supported")
            pix = mvc1(buf, w, h, last)
        except MvcError:
            frames.append({"state": "error", "crc32": None})
            continue
        if tag == b"MVC1":
            img = b"".join(bytes(((v >> 10 & 31) << 3 | (v >> 12 & 7), (v >> 5 & 31) << 3 | (v >> 7 & 7), (v & 31) << 3 | (v >> 2 & 7))) for v in pix)
        else:
            img = b"".join(bytes((v >> 16 & 0xFF, v >> 8 & 0xFF, v & 0xFF)) for v in pix)
        frames.append({"state": "decoded", "crc32": "%08x" % zlib.crc32(img)})
    return {"codec": tag.decode(), "width": w, "height": h, "frames": frames}
