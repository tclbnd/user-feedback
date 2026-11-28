"""PC Paint / Pictor pictures (.pic): CGA, EGA and VGA palettes, bit planes, block run-length coding."""
import zlib

CGA = [0x000000, 0x0000AA, 0x00AA00, 0x00AAAA, 0xAA0000, 0xAA00AA, 0xAA5500, 0xAAAAAA,
       0x555555, 0x5555FF, 0x55FF55, 0x55FFFF, 0xFF5555, 0xFF55FF, 0xFFFF55, 0xFFFFFF]
EGA = []
for _i in range(64):
    _r = (0xAA if _i & 4 else 0) + (0x55 if _i & 32 else 0)
    _g = (0xAA if _i & 2 else 0) + (0x55 if _i & 16 else 0)
    _b = (0xAA if _i & 1 else 0) + (0x55 if _i & 8 else 0)
    EGA.append(_r << 16 | _g << 8 | _b)
MODE45 = ((0, 3, 5, 7), (0, 2, 4, 6), (0, 3, 4, 7), (0, 11, 13, 15), (0, 10, 12, 14), (0, 11, 12, 15))


class PicError(ValueError):
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

    def u16(self):
        return self.u8() | self.u8() << 8

    def peek(self):
        return self.d[self.p] if self.p < len(self.d) else 0


def rgb(v):
    return (v >> 16 & 0xFF, v >> 8 & 0xFF, v & 0xFF)


class Canvas:
    def __init__(self, w, h, planes, bpp):
        self.w, self.h, self.planes, self.bpp = w, h, planes, bpp
        self.pix = bytearray(w * h)
        self.x, self.y, self.plane = 0, 0, 0

    def run8(self, value, run):
        while run > 0:
            if self.x + run >= self.w:
                n = self.w - self.x
                for k in range(n):
                    self.pix[self.y * self.w + self.x + k] = value
                run -= n
                self.x = 0
                self.y += 1
                if self.y >= self.h:
                    return True
            else:
                for k in range(run):
                    self.pix[self.y * self.w + self.x + k] = value
                self.x += run
                return False
        return False

    def runbits(self, value, run):
        bpp = self.bpp
        mask = (1 << bpp) - 1
        while run > 0:
            for j in range(8 - bpp, -1, -bpp):
                if self.plane >= self.planes:
                    return
                self.pix[self.y * self.w + self.x] |= (value >> j) & mask
                self.x += 1
                if self.x == self.w:
                    self.x = 0
                    self.y += 1
                    if self.y >= self.h:
                        self.y = 0
                        self.plane += 1
                        if self.plane >= self.planes:
                            return
            run -= 1


def parse(data):
    g = Bytes(data)
    if g.left() < 11:
        raise PicError("header is truncated")
    if g.u16() != 0x1234:
        raise PicError("not a Pictor picture")
    w, h = g.u16(), g.u16()
    g.p += 4
    t = g.u8()
    bpp_plane, planes = t & 0xF, (t >> 4) + 1
    bpp = bpp_plane * planes
    if bpp_plane > 8 or bpp < 1 or bpp > 32:
        raise PicError("unsupported bit depth")
    if not w or not h:
        raise PicError("empty picture")
    if g.peek() == 0xFF or bpp in (1, 4, 8):
        g.p += 2
        etype, esize = g.u16(), g.u16()
        if g.left() < esize:
            raise PicError("palette data is truncated")
    else:
        etype, esize = -1, 0
    after = g.p + esize
    pal = [(0, 0, 0)] * 256
    if etype in (1, 2):
        for i in range(min(esize, 16)):
            pal[i] = rgb(CGA[min(g.u8(), 15)])
    elif etype == 3:
        for i in range(min(esize, 16)):
            pal[i] = rgb(CGA[g.u8() & 15])
    elif etype in (4, 5):
        for i in range(min(esize // 3, 256)):
            pal[i] = tuple((g.u8() << 2) & 0xFF for _ in range(3))
    else:
        if bpp == 1:
            pal[0], pal[1] = (0, 0, 0), (255, 255, 255)
        elif bpp == 2:
            for i in range(4):
                pal[i] = rgb(CGA[i])
        else:
            for i in range(16):
                pal[i] = rgb(CGA[i])
    g.p = after
    c = Canvas(w, h, planes, bpp_plane)
    val = 0
    if g.u16():
        while g.left() >= 6:
            t1 = g.left()
            t2 = g.u16()
            stop = t1 - min(t1, t2)
            g.p += 2
            marker = g.u8()
            done = False
            while c.plane < planes and g.left() > stop:
                run = 1
                val = g.u8()
                if val == marker:
                    run = g.u8()
                    if run == 0:
                        run = 256
                    val = g.u8()
                if bpp_plane == 8:
                    if c.run8(val, run):
                        done = True
                        break
                else:
                    c.runbits(val, run)
            if done:
                break
        else:
            if c.plane < planes and c.x < w:
                run = (h - c.y) * w - c.x
                if bpp_plane == 8:
                    c.run8(val, run)
                else:
                    c.runbits(val, run // (8 // bpp_plane))
    else:
        y = 0
        while y < h and g.left() > 0:
            row = g.d[g.p:g.p + w]
            c.pix[y * w:y * w + len(row)] = row
            g.p += w
            y += 1
    img = b"".join(bytes(pal[v]) for v in c.pix)
    return {"width": w, "height": h, "planes": planes, "bits": bpp, "palette_type": etype,
            "crc32": "%08x" % zlib.crc32(img), "first_row": "%08x" % zlib.crc32(img[:3 * w]),
            "colours": len(set(c.pix))}
