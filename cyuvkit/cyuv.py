"""Creative YUV (CYUV) and Auravision Aura (AURA) frames.

Frames are coded as delta tables with 4:1:1 prediction, or stored
uncompressed as UYVY.  AURA uses the same coded layout as CYUV but
exchanges the U and V planes.
"""
import struct
import zlib


class CyuvError(ValueError):
    pass


def s8(v):
    return v - 256 if v > 127 else v


def frame(buf, w, h, swap_uv=False):
    raw = h * ((w + 1) & ~1) * 2
    if len(buf) >= 48 + h * (w * 3 // 4) and len(buf) != raw:
        yt = [s8(v) for v in buf[0:16]]
        ut = [s8(v) for v in buf[16:32]]
        vt = [s8(v) for v in buf[32:48]]
        Y, U, V = [], [], []
        p = 48
        for _ in range(h):
            c = buf[p]; p += 1
            y = c & 0xF0
            u = (c & 0x0F) << 4
            Y.append(y); U.append(u)
            c = buf[p]; p += 1
            v = c & 0xF0
            V.append(v)
            y = (y + yt[c & 0x0F]) & 0xFF; Y.append(y)
            c = buf[p]; p += 1
            y = (y + yt[c & 0x0F]) & 0xFF; Y.append(y)
            y = (y + yt[c >> 4]) & 0xFF; Y.append(y)
            for _ in range(w // 4 - 1):
                c = buf[p]; p += 1
                u = (u + ut[c >> 4]) & 0xFF; U.append(u)
                y = (y + yt[c & 0x0F]) & 0xFF; Y.append(y)
                c = buf[p]; p += 1
                v = (v + vt[c >> 4]) & 0xFF; V.append(v)
                y = (y + yt[c & 0x0F]) & 0xFF; Y.append(y)
                c = buf[p]; p += 1
                y = (y + yt[c & 0x0F]) & 0xFF; Y.append(y)
                y = (y + yt[c >> 4]) & 0xFF; Y.append(y)
        if swap_uv:
            U, V = V, U
        return "yuv411p", bytes(Y) + bytes(U) + bytes(V)
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
    if tag == b"CYUV":
        swap_uv = False
    elif tag == b"AURA":
        swap_uv = True
    else:
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
            fmt, pic = frame(buf, w, h, swap_uv)
        except CyuvError:
            frames.append({"state": "error", "crc32": None})
            continue
        frames.append({"state": "decoded", "format": fmt, "crc32": "%08x" % zlib.crc32(pic)})
    return {"width": w, "height": h, "codec": tag.decode(), "frames": frames}
