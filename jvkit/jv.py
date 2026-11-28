"""Bitmap Brothers JV movies (.jv): quadtree-coded 8-bit frames and 6-bit palettes."""
import zlib


class JvError(ValueError):
    pass


class Bits:
    def __init__(self, d):
        self.d, self.p = d, 0

    def get(self, n):
        v = 0
        for _ in range(n):
            byte = self.d[self.p >> 3] if (self.p >> 3) < len(self.d) else 0
            v = v << 1 | (byte >> (7 - (self.p & 7))) & 1
            self.p += 1
        return v


def block(g, pic, w, x, y, size):
    op = g.get(2)
    if op == 0:
        for j in range(size):
            pic[(y + j) * w + x:(y + j) * w + x + size] = bytes(size)
        return
    if op == 1:
        v = g.get(8)
        for j in range(size):
            pic[(y + j) * w + x:(y + j) * w + x + size] = bytes([v]) * size
    elif op == 2:
        v = (g.get(8), g.get(8))
        rows = range(size)
        for j in rows:
            for i in range(size):
                pic[(y + j) * w + x + i] = v[g.get(1)]
    else:
        if size == 2:
            for j in range(2):
                for i in range(2):
                    pic[(y + j) * w + x + i] = g.get(8)
        else:
            h = size // 2
            for j in (0, h):
                for i in (0, h):
                    block(g, pic, w, x + i, y + j, h)


def parse(data):
    if len(data) < 14 or data[:8] != b"JVMOVIE\0":
        raise JvError("not a JV movie")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    count = int.from_bytes(data[12:14], "little")
    if not w or not h or w % 8 or h % 8:
        raise JvError("width and height must be non-zero multiples of 8")
    pic = bytearray(w * h)
    pal = [(0, 0, 0)] * 256
    pos = 14
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise JvError("packet table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        pkt = data[pos + 4:pos + 4 + n]
        if len(pkt) < n:
            raise JvError("packet is truncated")
        pos += 4 + n
        if len(pkt) < 6:
            frames.append({"ok": False, "crc32": None})
            continue
        vsize = int.from_bytes(pkt[0:4], "little")
        vtype = pkt[4]
        p = 5
        ok = True
        if vsize:
            if vsize > len(pkt) - 5:
                frames.append({"ok": False, "crc32": None})
                continue
            if vtype in (0, 1):
                g = Bits(pkt[p:p + vsize])
                for y in range(0, h, 8):
                    for x in range(0, w, 8):
                        block(g, pic, w, x, y, 8)
                p += vsize
            else:
                frames.append({"ok": False, "crc32": None})
                continue
        if len(pkt) - p >= 768:
            pal = []
            for k in range(256):
                c = pkt[p + 3 * k:p + 3 * k + 3]
                pal.append(tuple((v << 2) & 0xFF for v in c))
        if True:
            rgb = b"".join(bytes(pal[v]) for v in pic)
            frames.append({"ok": ok, "crc32": "%08x" % zlib.crc32(rgb)})
    return {"width": w, "height": h, "frames": frames}
