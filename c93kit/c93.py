"""Interplay C93 movies (.c93): 320 x 192 frames coded as 8 x 8 blocks over two alternating pictures."""
import zlib

W, H = 320, 192


class C93Error(ValueError):
    pass


class Reader:
    def __init__(self, d):
        self.d, self.p = d, 0

    def byte(self):
        v = self.d[self.p] if self.p < len(self.d) else 0
        self.p += 1
        return v

    def le(self, n):
        v = 0
        for k in range(n):
            v |= self.byte() << (8 * k)
        return v

    def take(self, n):
        out = bytes(self.d[self.p:self.p + n]).ljust(n, b"\0")
        self.p += n
        return out


class Decoder:
    def __init__(self):
        self.pics = [None, None]
        self.pals = [None, None]
        self.cur = 0

    def copy(self, to, tx, ty, src, offset, size):
        if src is None:
            src = bytes(W * H)
        fx, fy = offset % W, offset // W
        width = min(size, W - fx)
        for i in range(min(size, H - fy)):
            to[(ty + i) * W + tx:(ty + i) * W + tx + width] = src[(fy + i) * W + fx:(fy + i) * W + fx + width]

    def colours(self, to, tx, ty, w, h, bpp, cols, bits, grps=None):
        total = w * h
        k = 0
        for y in range(h):
            if grps:
                cols[0] = grps[3 * (y >> 1)]
            for x in range(w):
                if grps:
                    cols[1] = grps[(x >> 1) + 1]
                to[(ty + y) * W + tx + x] = cols[(bits >> ((total - 1 - k) * bpp)) & ((1 << bpp) - 1)]
                k += 1

    def frame(self, pkt):
        self.cur ^= 1
        new_i, old_i = self.cur ^ 1, self.cur
        if self.pics[new_i] is None:
            self.pics[new_i] = bytearray(W * H)
        new, old = self.pics[new_i], self.pics[old_i]
        r = Reader(pkt)
        b = r.byte()
        queue = []
        for y in range(0, H, 8):
            for x in range(0, W, 8):
                if not queue:
                    v = r.byte()
                    queue = [v >> 4, v & 0x0F]
                t = queue.pop(0)
                if t == 0x02:
                    self.copy(new, x, y, old, r.le(2), 8)
                elif t in (0x06, 0x07):
                    src = new if t == 0x07 else old
                    for i in (0, 4):
                        for j in (0, 4):
                            self.copy(new, x + i, y + j, src, r.le(2), 4)
                elif t == 0x08:
                    cols = list(r.take(2))
                    for i in range(8):
                        self.colours(new, x, y + i, 8, 1, 1, cols, r.byte())
                elif t in (0x0A, 0x0B, 0x0D):
                    for j in (0, 4):
                        for i in (0, 4):
                            if t == 0x0A:
                                cols = list(r.take(2))
                                self.colours(new, x + i, y + j, 4, 4, 1, cols, r.le(2))
                            elif t == 0x0D:
                                cols = list(r.take(4))
                                self.colours(new, x + i, y + j, 4, 4, 2, cols, r.le(4))
                            else:
                                grps = list(r.take(4))
                                self.colours(new, x + i, y + j, 4, 4, 1, [0, 0, 0, 0], r.le(2), grps)
                elif t == 0x0E:
                    self.copy(new, x, y, old, y * W + x, 8)
                elif t == 0x0F:
                    for j in range(8):
                        new[(y + j) * W + x:(y + j) * W + x + 8] = r.take(8)
                else:
                    raise C93Error("unknown block type %d" % t)
        if b & 1:
            self.pals[new_i] = [tuple(r.take(3)) for _ in range(256)]
        else:
            self.pals[new_i] = [(0, 0, 0)] * 256
        return bool(b & 2), new_i


def parse(data):
    if len(data) < 10 or data[:8] != b"C93VIDEO":
        raise C93Error("not a C93 video pack")
    count = data[8] | data[9] << 8
    pos = 10
    dec = Decoder()
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise C93Error("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise C93Error("frame data is truncated")
        pos += 4 + n
        try:
            key, idx = dec.frame(body)
            pal, pic = dec.pals[idx], dec.pics[idx]
            rgb = b"".join(bytes(pal[v]) for v in pic)
            frames.append({"ok": True, "key": key, "crc32": "%08x" % zlib.crc32(rgb)})
        except C93Error:
            frames.append({"ok": False, "key": None, "crc32": None})
    return {"width": W, "height": H, "frames": frames}
