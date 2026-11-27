"""Apple Graphics (SMC) frame sequences (.smc): decode each frame to 8-bit palette indices."""
import zlib

MAGIC = b"SMCSEQ\0\0"


class SmcError(ValueError):
    pass


class Reader:
    def __init__(self, data, pos=0):
        self.d, self.p = data, pos

    def left(self):
        return len(self.d) - self.p

    def byte(self):
        if self.p >= len(self.d):
            raise SmcError("frame data ends early")
        v = self.d[self.p]
        self.p += 1
        return v

    def be16(self):
        return self.byte() << 8 | self.byte()

    def be32(self):
        return self.be16() << 16 | self.be16()


class Decoder:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.pix = bytearray(w * h)
        self.pairs = bytearray(256 * 2)
        self.quads = bytearray(256 * 4)
        self.octets = bytearray(256 * 8)

    def frame(self, data):
        w = self.w
        r = Reader(data, 4)
        if len(data) < 4:
            raise SmcError("frame header is truncated")
        total = (w // 4) * (self.h // 4)
        row = col = 0
        pi = qi = oi = 0
        pix = self.pix

        def advance(n_left):
            nonlocal row, col, total
            col += 4
            if col >= w:
                col = 0
                row += w * 4
            total -= 1
            if total < (1 if n_left else 0):
                raise SmcError("more blocks than the picture has")

        def paint(get):
            b = row + col
            for y in range(4):
                for x in range(4):
                    pix[b + y * w + x] = get(y, x)

        def copy(src):
            b = row + col
            for y in range(4):
                for x in range(4):
                    pix[b + y * w + x] = pix[src + y * w + x]

        def count(op):
            return r.byte() if op & 0x10 else 1 + (op & 0x0F)

        while total:
            if row >= w * self.h:
                raise SmcError("row pointer left the picture")
            op = r.byte()
            kind = op & 0xF0
            if kind in (0x00, 0x10):
                n = count(op)
                while n:
                    n -= 1
                    paint(lambda y, x: 0)
                    advance(n)
            elif kind in (0x20, 0x30):
                n = count(op)
                prev = row - w * 4 + w - 4 if col == 0 else row + col - 4
                while n:
                    n -= 1
                    copy(prev)
                    advance(n)
            elif kind in (0x40, 0x50):
                n = count(op) * 2
                if col == 0:
                    p1 = row - w * 4 + w - 8
                elif col == 4:
                    p1 = row - w * 4 + w - 4
                else:
                    p1 = row + col - 8
                p2 = row - w * 4 + w - 4 if col == 0 else row + col - 4
                flag = False
                while n:
                    n -= 1
                    copy(p1 if flag else p2)
                    flag = not flag
                    advance(n)
            elif kind in (0x60, 0x70):
                n = count(op)
                c = r.byte()
                while n:
                    n -= 1
                    paint(lambda y, x: c)
                    advance(n)
            elif kind in (0x80, 0x90):
                n = (op & 0x0F) + 1
                base = pi * 2
                self.pairs[base:base + 2] = bytes([r.byte(), r.byte()])
                pi += 1
                while n:
                    n -= 1
                    f = r.be16()
                    paint(lambda y, x: self.pairs[base + (f >> (y * 4 + x) & 1)])
                    advance(n)
            elif kind in (0xA0, 0xB0):
                n = (op & 0x0F) + 1
                base = qi * 4
                self.quads[base:base + 4] = bytes(r.byte() for _ in range(4))
                qi += 1
                while n:
                    n -= 1
                    f = r.be32()
                    paint(lambda y, x: self.quads[base + (f >> (2 * (y * 4 + x)) & 3)])
                    advance(n)
            elif kind in (0xC0, 0xD0):
                raise SmcError("8-colour blocks are not supported")
            else:
                n = (op & 0x0F) + 1
                while n:
                    n -= 1
                    vals = [r.byte() for _ in range(16)]
                    paint(lambda y, x: vals[y * 4 + x])
                    advance(n)


def parse(data):
    if len(data) < 16 or data[:8] != MAGIC:
        raise SmcError("not an SMC frame sequence")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    count = int.from_bytes(data[12:16], "little")
    if not w or not h or w % 4 or h % 4:
        raise SmcError("width and height must be non-zero multiples of 4")
    dec = Decoder(w, h)
    pos, frames = 16, []
    for _ in range(count):
        if pos + 4 > len(data):
            raise SmcError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise SmcError("frame data is truncated")
        pos += 4 + n
        try:
            dec.frame(body)
            ok = True
        except SmcError:
            ok = False
        frames.append({"ok": ok, "crc32": "%08x" % zlib.crc32(bytes(dec.pix))})
    hist = {}
    for v in dec.pix:
        hist[v] = hist.get(v, 0) + 1
    return {"width": w, "height": h, "frames": frames, "colours": len(hist),
            "top": [[k, v] for k, v in sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))[:4]]}
