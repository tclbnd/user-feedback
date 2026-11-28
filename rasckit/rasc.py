"""RemotelyAnywhere screen capture (RASC) packets: FINT, KFRM, DLTA, MOUS and MPOS chunks over two frame buffers."""
import struct
import zlib

PAD = 64


class RascError(ValueError):
    pass


class In:
    def __init__(self, d, p=0, end=None):
        self.d, self.p = d, p
        self.end = len(d) if end is None else end

    def left(self):
        return self.end - self.p

    def byte(self):
        v = self.d[self.p] if self.p < self.end else 0
        self.p += 1
        return v

    def le16(self):
        return self.byte() | self.byte() << 8

    def le32(self):
        return self.le16() | self.le16() << 16

    def peek32(self):
        q = self.p
        v = self.le32()
        self.p = q
        return v


def inflate(data, size):
    try:
        out = zlib.decompress(data)
    except zlib.error:
        raise RascError("inflate failed")
    return out[:size]


class Rasc:
    def __init__(self):
        self.w = self.h = 0
        self.f1 = self.f2 = None
        self.pal = [0] * 256
        self.cursor = None
        self.cx = self.cy = 0

    def new_frames(self):
        self.f1 = [bytearray(self.stride + PAD) for _ in range(self.h)]
        self.f2 = [bytearray(self.stride + PAD) for _ in range(self.h)]

    def clear(self):
        for f in (self.f1, self.f2):
            for r in f:
                r[:] = bytes(len(r))

    def fint(self, g):
        if g.peek32() != 0x65:
            return
        if g.left() < 72:
            raise RascError("frame info is truncated")
        g.p += 8
        w, h = g.le32(), g.le32()
        g.p += 30
        fmt = g.le16()
        g.p += 24
        if fmt == 8:
            stride, bpp = w, 1
        elif fmt == 16:
            stride, bpp = w * 2, 2
        elif fmt == 32:
            stride, bpp = w * 4, 4
        else:
            raise RascError("unsupported pixel format")
        if not w or not h or w > 4096 or h > 4096:
            raise RascError("bad frame size")
        self.w, self.h, self.fmt, self.stride, self.bpp = w, h, fmt, stride, bpp
        self.new_frames()
        if fmt == 8:
            self.pal = [g.le32() & 0xFFFFFF for _ in range(256)]

    def kfrm(self, g, size):
        pos = g.p
        if g.peek32() == 0x65:
            self.fint(g)
        if self.f2 is None:
            raise RascError("no frame set up")
        d = zlib.decompressobj()
        try:
            out = d.decompress(g.d[g.p:g.end])
        except zlib.error:
            raise RascError("inflate failed")
        k = 0
        for f in (self.f2, self.f1):
            for i in range(self.h):
                row = f[i]
                part = out[k:k + self.stride]
                if len(part) < self.stride and not d.eof:
                    raise RascError("key frame data ran out")
                row[:len(part)] = part
                k += len(part)
        g.p = pos + size

    def dlta(self, g, size):
        pos = g.p
        g.p += 12
        usize = g.le32()
        x, y, w, h = g.le32(), g.le32(), g.le32(), g.le32()
        if x >= self.w or y >= self.h or w > self.w or h > self.h or x + w > self.w or y + h > self.h:
            raise RascError("delta rectangle is outside the frame")
        g.p += 4
        comp = g.le32()
        if comp in (0, 1):
            if g.left() < usize:
                raise RascError("delta is truncated")
            dc = In(g.d, g.p, g.p + usize)
        else:
            raise RascError("unsupported delta compression")
        if self.f1 is None:
            raise RascError("no frame set up")
        bpp = self.bpp
        row = y
        cx, cy = 0, h
        xo = x * bpp
        f1, f2 = self.f1, self.f2

        def nxt():
            nonlocal cx, cy, row
            if cx >= w * bpp:
                cx = 0
                cy -= 1
                row += 1

        def word(f, o):
            r = f[row]
            return bytes(r[xo + o:xo + o + 4])

        def put(f, o, b):
            r = f[row]
            r[xo + o:xo + o + 4] = b
        while dc.left() > 0:
            t, n = dc.byte(), dc.byte()
            if t == 1:
                while n > 0 and cy > 0:
                    cx += 1; nxt(); n -= 1
            elif t == 2:
                while n > 0 and cy > 0:
                    f2[row][xo + cx] = f1[row][xo + cx]
                    cx += 1; nxt(); n -= 1
            elif t == 3:
                while n > 0 and cy > 0:
                    f2[row][xo + cx] = dc.byte()
                    cx += 1; nxt(); n -= 1
            elif t == 4:
                v = dc.byte()
                while n > 0 and cy > 0:
                    f1[row][xo + cx] = f2[row][xo + cx]
                    f2[row][xo + cx] = v
                    cx += 1; nxt(); n -= 1
            elif t == 7:
                v = struct.pack("<I", dc.le32())
                while n > 0 and cy > 0:
                    put(f1, cx, word(f2, cx))
                    put(f2, cx, v)
                    cx += 4; nxt(); n -= 1
            elif t == 10:
                while n > 0 and cy > 0:
                    cx += 4; nxt(); n -= 1
            elif t == 12:
                while n > 0 and cy > 0:
                    put(f2, cx, word(f1, cx))
                    cx += 4; nxt(); n -= 1
            elif t == 13:
                while n > 0 and cy > 0:
                    put(f2, cx, struct.pack("<I", dc.le32()))
                    cx += 4; nxt(); n -= 1
            else:
                raise RascError("unknown delta operation")
        g.p = pos + size

    def mous(self, g, size):
        pos = g.p
        g.p += 8
        w, h = g.le32(), g.le32()
        g.p += 12
        usize = g.le32()
        if w > self.w or h > self.h or usize != 3 * w * h:
            raise RascError("bad cursor")
        data = inflate(g.d[g.p:g.end][:size - (g.p - pos)], usize)
        if len(data) < usize:
            data = data + bytes(usize - len(data))
        self.cursor = (w, h, data)
        g.p = pos + size

    def mpos(self, g, size):
        pos = g.p
        g.p += 8
        self.cx, self.cy = g.le32(), g.le32()
        g.p = pos + size

    def picture(self):
        w, h, bpp = self.w, self.h, self.bpp
        rows = [bytearray(self.f2[y][:w * bpp]) for y in range(h)]
        out = bytearray()
        for r in rows:
            for x in range(w):
                if bpp == 1:
                    v = self.pal[r[x]]
                    out += bytes([v >> 16 & 0xFF, v >> 8 & 0xFF, v & 0xFF])
                elif bpp == 2:
                    v = r[2 * x] | r[2 * x + 1] << 8
                    out += bytes(((c << 3) | (c >> 2)) for c in (v >> 10 & 31, v >> 5 & 31, v & 31))
                else:
                    out += bytes([r[4 * x + 2], r[4 * x + 1], r[4 * x]])
        return bytes(out)

    def packet(self, pkt):
        g = In(pkt)
        while g.left() > 0:
            if g.left() < 8:
                raise RascError("chunk header is truncated")
            t = g.d[g.p:g.p + 4]
            g.p += 4
            if t in (b"KBND", b"BNDL"):
                t = g.d[g.p:g.p + 4]
                g.p += 4
            size = g.le32()
            if g.left() < size:
                raise RascError("chunk is truncated")
            if t in (b"FINT", b"INIT"):
                self.fint(g)
            elif t == b"KFRM":
                self.kfrm(g, size)
            elif t == b"DLTA":
                self.dlta(g, size)
            elif t == b"MOUS":
                self.mous(g, size)
            elif t == b"MPOS":
                self.mpos(g, size)
            else:
                g.p += size
        if self.f1 is None:
            raise RascError("no frame set up")
        return self.picture()


def parse(data):
    if data[:8] != b"RASCPACK":
        raise RascError("not a RASC pack")
    count, = struct.unpack_from("<H", data, 8)
    v = Rasc()
    pos = 10
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise RascError("packet table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        pkt = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(pkt) < n:
            raise RascError("packet is truncated")
        try:
            img = v.packet(pkt)
        except RascError:
            frames.append({"state": "error", "crc32": None})
            continue
        if img is None:
            frames.append({"state": "empty"})
            continue
        frames.append({"state": "decoded", "width": v.w, "height": v.h, "crc32": "%08x" % zlib.crc32(img)})
    return {"frames": frames}
