"""Creative YUV (CYUV) frames: delta tables with 4:1:1 prediction, or raw UYVY frames."""
import struct
import zlib


class CyuvError(ValueError):
    pass


def s8(v):
    return v


def frame(buf, w, h):
    raw = h * ((w + 1) & ~1) * 2
    if len(buf) >= 48 + h * (w * 3 // 4) and len(buf) != raw:
        yt = [s8(v) for v in buf[0:16]]
        ut = [s8(v) for v in buf[16:32]]
        vt = [s8(v) for v in buf[32:48]]
        Y, U, V = [], [], []
        p = 48
        for _ in range(h):
            c = buf[p]; p += 1
            v = c & 0xF0
            y = c & 0x0F
            V.append(v); Y.append(y)
            c = buf[p]; p += 1
            u = c & 0xF0
            U.append(u)
            y = (y + yt[c & 0x0F]); Y.append(y)
            c = buf[p]; p += 1
            y = (y + yt[c >> 4]); Y.append(y)
            y = (y + yt[c & 0x0F]); Y.append(y)
            for _ in range(w // 4 - 1):
                c = buf[p]; p += 1
                u = (u + ut[c >> 4]); U.append(u)
                y = (y + yt[c & 0x0F]); Y.append(y)
                c = buf[p]; p += 1
                v = (v + vt[c >> 4]); V.append(v)
                y = (y + yt[c & 0x0F]); Y.append(y)
                c = buf[p]; p += 1
                y = (y + yt[c >> 4]); Y.append(y)
                y = (y + yt[c & 0x0F]); Y.append(y)
        clip = lambda a: bytes(min(255, max(0, x)) for x in a)
        return "yuv411p", clip(Y) + clip(U) + clip(V)
    if len(buf) == raw:
        ls = ((w + 1) & ~1) * 2
        rows = [buf[k:k + ls] for k in range(0, raw, ls)]
        return "uyvy", b"".join(rows)
    raise CyuvError("frame size does not match the picture")


def parse(data):
    if data[:8] != b"CYUVPACK":
        raise CyuvError("not a CYUV pack")
    w, h = struct.unpack_from("<HH", data, 8)
    tag = data[12:16]
    count, = struct.unpack_from("<H", data, 16)
    if tag != b"CYUV":
        raise CyuvError("unknown codec")
    if not w or not h:
        raise CyuvError("width must be a multiple of 4")
    pos = 18
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise CyuvError("frame table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        buf = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(buf) < n:
            raise CyuvError("frame is truncated")
        try:
            fmt, pic = frame(buf, w, h)
        except CyuvError:
            frames.append({"state": "error", "crc32": None})
            continue
        frames.append({"state": "decoded", "format": fmt, "crc32": "%08x" % zlib.crc32(pic)})
    return {"width": w, "height": h, "codec": tag.decode(), "frames": frames}
