"""Apple Video (RPZA, "Road Pizza") frame sequences (.rpz): decode each frame to RGB555."""
import zlib

MAGIC = b"RPZASEQ\0"


class RpzaError(ValueError):
    pass


class Reader:
    def __init__(self, data):
        self.d, self.p = data, 0

    def left(self):
        return len(self.d) - self.p

    def byte(self):
        if self.p >= len(self.d):
            raise RpzaError("chunk ends early")
        v = self.d[self.p]
        self.p += 1
        return v

    def peek(self):
        return self.d[self.p] if self.p < len(self.d) else 0

    def be16(self):
        return self.byte() << 8 | self.byte()


def _blend(a, b):
    c = [a, 0, 0, b]
    for sh in (10, 5, 0):
        ta, tb = a >> sh & 0x1F, b >> sh & 0x1F
        c[1] |= ((ta + tb) >> 1) << sh
        c[2] |= ((ta + tb) >> 1) << sh
    return c


def decode(pic, stride, w, h, data):
    r = Reader(data)
    if r.left() < 4:
        raise RpzaError("frame is too short")
    r.p = 4
    total = (w // 4) * (h // 4)
    row = col = 0
    colour_a = 0

    def advance():
        nonlocal row, col, total
        col += 4
        if col + 4 > w:
            col = 0
            row += 4
        total -= 1

    def check():
        pass

    def put(y, x, v):
        pic[(row + y) * stride + col + x] = v

    while r.left():
        op = r.byte()
        n = op & 0x1F
        if not op & 0x80:
            colour_a = op << 8 | r.byte()
            op = 0
        kind = op & 0xE0
        if kind == 0x80:
            for _ in range(n):
                check()
                for y in range(4):
                    for x in range(4):
                        put(y, x, 0)
                advance()
        elif kind == 0xA0:
            colour_a = r.be16()
            for _ in range(n):
                check()
                for y in range(4):
                    for x in range(4):
                        put(y, x, colour_a)
                advance()
        elif kind in (0xC0, 0x20):
            if kind == 0xC0:
                colour_a = r.be16()
            c4 = _blend(colour_a, r.be16())
            if r.left() < n * 4:
                raise RpzaError("not enough index bytes")
            for _ in range(n):
                check()
                for y in range(4):
                    idx = r.byte()
                    for x in range(4):
                        put(y, x, c4[idx >> (2 * x) & 3])
                advance()
        elif kind == 0x00:
            if r.left() < 30:
                raise RpzaError("16-colour block is truncated")
            check()
            for y in range(4):
                for x in range(4):
                    if y or x:
                        colour_a = r.be16()
                    put(y, x, colour_a)
            advance()
        else:
            continue


def parse(data):
    if len(data) < 16 or data[:8] != MAGIC:
        raise RpzaError("not an RPZA frame sequence")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    count = int.from_bytes(data[12:16], "little")
    if not w or not h or h % 4:
        raise RpzaError("width must be non-zero and height a non-zero multiple of 4")
    stride = (w + 3) // 4 * 4
    pic = [0] * (stride * h)
    pos, frames = 16, []
    for _ in range(count):
        if pos + 4 > len(data):
            raise RpzaError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise RpzaError("frame data is truncated")
        pos += 4 + n
        try:
            decode(pic, stride, w, h, body)
            ok = True
        except RpzaError:
            ok = False
        img = [pic[y * stride + x] for y in range(h) for x in range(w)]
        frames.append({"ok": ok, "crc32": "%08x" % zlib.crc32(b"".join(v.to_bytes(2, "little") for v in img))})
    img = [pic[y * stride + x] for y in range(h) for x in range(w)]
    return {"width": w, "height": h, "frames": frames, "colours": len(set(img)),
            "mean_rgb": [round(sum(v >> s & 0x1F for v in img) / len(img), 2) for s in (10, 5, 0)]}
