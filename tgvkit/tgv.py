"""Electronic Arts TGV video chunks: kVGT intra frames (palette + RefPack-style LZ) and inter frames (vectors and block codebooks)."""
import struct
import zlib


class TgvError(ValueError):
    pass


class Bits:
    """Big-endian bit reader, high bit first."""
    def __init__(self, d, p, nbits):
        self.d, self.base, self.n, self.i = d, p, nbits, 0

    def get(self, k):
        v = 0
        for _ in range(k):
            byte = self.base + (self.i >> 3)
            bit = (self.d[byte] >> (7 - (self.i & 7)) & 1) if self.i < self.n and byte < len(self.d) else 0
            v = v << 1 | bit
            self.i += 1
        return v

    def sget(self, k):
        v = self.get(k)
        return v - (1 << k) if v >> (k - 1) else v

    def left(self):
        return self.n - self.i


def unpack(src, p, dst, w, h):
    end = len(src)
    dend = w * h
    p += 5 if src[p] & 1 else 2
    if end - p < 3:
        raise TgvError("intra data is truncated")
    size = src[p] << 16 | src[p + 1] << 8 | src[p + 2]
    p += 3
    d = 0
    g = lambda k: src[k] if k < end else 0
    while size > 0 and p < end:
        b0 = src[p]
        s1 = b0 & 3
        s2 = 0
        off = 0
        if b0 & 0x80:
            if b0 & 0x40:
                if b0 & 0x20:
                    s1 = ((b0 & 31) + 1) << 2
                    p += 1
                else:
                    off = (g(p + 1) << 8 | g(p + 2)) + 1
                    s2 = g(p + 3) + 5
                    p += 4
            else:
                off = ((g(p + 1) << 8 | g(p + 2)) & 0x3FFF) + 1
                s2 = (b0 & 0x3F) + 4
                p += 3
        else:
            off = ((b0 & 0x60) << 3) + g(p + 1) + 1
            s2 = ((b0 & 0x1C) >> 2) + 3
            p += 2
        if s1 > end - p:
            break
        if s1 > 0:
            size -= s1
            run = min(s1, dend - d)
            dst[d:d + run] = src[p:p + run]
            d += run
            p += run
        if s2 > 0:
            if d < off or off <= 0:
                raise TgvError("back reference before the start")
            size -= s2
            run = min(s2, dend - d)
            dst[d:d + run] = dst[d - off:d - off + run]
            d += run


class Tgv:
    def __init__(self):
        self.w = self.h = 0
        self.pal = [0] * 256
        self.fb = None
        self.last = None

    def chunk(self, buf):
        if len(buf) < 8:
            raise TgvError("chunk is too short")
        tag = buf[:4]
        p = 8
        if tag == b"kVGT":
            if len(buf) - p < 12:
                raise TgvError("intra header is truncated")
            w, h = struct.unpack_from("<HH", buf, p)
            if (w, h) != (self.w, self.h):
                self.w, self.h = w, h
                self.fb = None
                self.last = None
            if not w or not h:
                raise TgvError("empty picture")
            n = buf[p + 6] | buf[p + 7] << 8
            p += 12
            i = 0
            while i < n and i < 256 and len(buf) - p >= 3:
                self.pal[i] = buf[p] << 16 | buf[p + 1] << 8 | buf[p + 2]
                p += 3
                i += 1
            if len(buf) - p < 5:
                raise TgvError("intra data is truncated")
            self.fb = bytearray(w * h)
            unpack(buf, p, self.fb, w, h)
            pic = bytearray(self.fb)
        else:
            if self.last is None:
                raise TgvError("inter frame before any intra frame")
            pic = self.inter(buf, p)
        self.last = pic
        return pic

    def inter(self, buf, p):
        w, h = self.w, self.h
        if len(buf) - p < 12:
            raise TgvError("inter header is truncated")
        nmv, nraw, npk, vb = struct.unpack_from("<4H", buf, p)
        p += 12
        mvbits = (nmv * 20 + 7) & ~7
        g = Bits(buf, p, mvbits)
        mvs = [(g.sget(10), g.sget(10)) for _ in range(nmv)]
        p += mvbits >> 3
        raw = p
        p += 16 * nraw
        g = Bits(buf, p, (len(buf) - p) * 8)
        books = []
        for _ in range(npk):
            tmp = [g.get(8) for _ in range(4)]
            blk = [0] * 16
            for j in range(16):
                blk[15 - j] = tmp[g.get(2)]
            books.append(blk)
        pic = bytearray(w * h)
        last = self.last
        for y in range(h // 4):
            for x in range(w // 4):
                v = g.get(vb)
                if v < nmv:
                    mx, my = x * 4 + mvs[v][0], y * 4 + mvs[v][1]
                    if mx < 0 or mx + 4 > w or my < 0 or my + 4 > h:
                        continue
                    blk = [last[(my + j) * w + mx + i] for j in range(4) for i in range(4)]
                else:
                    o = v - nmv
                    if o < nraw:
                        blk = list(buf[raw + 16 * o:raw + 16 * o + 16])
                    elif o - nraw < npk:
                        blk = books[o - nraw]
                    else:
                        continue
                for j in range(4):
                    pic[(y * 4 + j) * w + x * 4:(y * 4 + j) * w + x * 4 + 4] = bytes(blk[4 * j:4 * j + 4])
        return pic


def parse(data):
    if data[:8] != b"TGVPACK1":
        raise TgvError("not a TGV pack")
    count, = struct.unpack_from("<H", data, 8)
    t = Tgv()
    pos = 10
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise TgvError("chunk table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        buf = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(buf) < n:
            raise TgvError("chunk is truncated")
        try:
            pic = t.chunk(buf)
        except TgvError:
            frames.append({"state": "error", "crc32": None})
            continue
        if pic is None:
            frames.append({"state": "skipped"})
            continue
        img = bytearray(3 * len(pic))
        for k, sh in enumerate((16, 8, 0)):
            img[k::3] = bytes(pic).translate(bytes(t.pal[c] >> sh & 0xFF for c in range(256)))
        frames.append({"state": "decoded", "width": t.w, "height": t.h, "crc32": "%08x" % zlib.crc32(bytes(img))})
    return {"frames": frames}
