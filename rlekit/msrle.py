"""Microsoft RLE video (RLE8 and RLE4 frames in game AVI files): bottom-up runs, literals, deltas onto a kept picture."""
import zlib


class RleError(ValueError):
    pass


class Bytes:
    def __init__(self, d):
        self.d, self.p = d, 0

    def left(self):
        return max(0, len(self.d) - self.p)

    def u8(self):
        v = self.d[self.p] if self.p < len(self.d) else 0
        self.p += 1
        return v


def rle8(pic, w, h, g):
    line, pos = 0, 0
    while g.left() > 0:
        p1 = g.u8()
        if p1 == 0:
            p2 = g.u8()
            if p2 == 0:
                line += 1
                if line >= h:
                    return "decoded"
                pos = 0
                continue
            if p2 == 1:
                return "decoded"
            if p2 == 2:
                dy, dx = g.u8(), g.u8()
                line += dy
                pos += dx
                if line >= h or pos >= w:
                    raise RleError("delta beyond the picture")
                continue
            p2 = min(p2, w - pos)
            if g.left() < p2:
                raise RleError("literal runs past the data")
            pic[line * w + pos:line * w + pos + p2] = g.d[g.p:g.p + p2]
            g.p += p2
            pos += p2
        else:
            v = g.u8()
            p1 = min(p1, w - pos)
            pic[line * w + pos:line * w + pos + p1] = bytes([v]) * p1
            pos += p1
    return "no end code"


def rle4(pic, w, h, g):
    line, ptr = h - 1, 0
    while line >= 0 and ptr <= w:
        if g.left() <= 0:
            raise RleError("data runs out")
        code = g.u8()
        if code == 0:
            sb = g.u8()
            if sb == 0:
                line -= 1
                ptr = 0
            elif sb == 1:
                return "decoded"
            elif sb == 2:
                line -= g.u8()
                ptr += g.u8()
            else:
                odd = sb & 1
                n = (sb + 1) // 2
                extra = n & 1
                if ptr + 2 * n - odd > w or g.left() < n:
                    raise RleError("literal runs past the line")
                for i in range(n):
                    if ptr >= w:
                        break
                    b = g.u8()
                    pic[line * w + ptr] = b & 0x0F
                    ptr += 1
                    if i + 1 == n and odd:
                        break
                    if ptr >= w:
                        break
                    pic[line * w + ptr] = b >> 4
                    ptr += 1
        else:
            if ptr + code > w + 1:
                raise RleError("run past the line")
            b = g.u8()
            for i in range(code):
                if ptr >= w:
                    break
                pic[line * w + ptr] = b & 0x0F if i % 2 == 0 else b >> 4
                ptr += 1
    return "decoded"


def parse(data):
    if len(data) < 16 + 1024 or data[:8] != b"MSRLEPAK":
        raise RleError("not an MS RLE frame pack")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    depth = data[12]
    count = int.from_bytes(data[14:16], "little")
    if depth not in (4, 8):
        raise RleError("depth must be 4 or 8")
    if not w or not h:
        raise RleError("empty picture")
    pal = [(data[16 + 4 * k + 2], data[16 + 4 * k + 1], data[16 + 4 * k]) for k in range(256)]
    pos = 16 + 1024
    pic = bytearray(w * h)
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise RleError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise RleError("frame data is truncated")
        pos += 4 + n
        pic = bytearray(w * h)
        try:
            state = (rle8 if depth == 8 else rle4)(pic, w, h, Bytes(body))
        except RleError:
            state = "error"
        rgb = b"".join(bytes(pal[v]) for v in pic)
        frames.append({"state": state, "crc32": "%08x" % zlib.crc32(rgb)})
    return {"width": w, "height": h, "depth": depth, "frames": frames}
