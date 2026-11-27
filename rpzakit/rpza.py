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
    # RPZA four-colour palette: colour A, two weighted transitions, colour B.
    c = [a, 0, 0, b]
    for i, weight_a in ((1, 2), (2, 1)):
        v = 0
        for sh in (10, 5, 0):
            ca, cb = (a >> sh) & 0x1F, (b >> sh) & 0x1F
            v |= ((weight_a * ca + (3 - weight_a) * cb + 1) // 3) << sh
        c[i] = v
    return c


def decode(pic, stride, w, h, data):
    r = Reader(data)
    if r.left() < 4:
        raise RpzaError("frame is too short")
    # Chunk header: 0xE1 marker followed by the chunk length as a 3-byte
    # big-endian integer, counting the four header bytes themselves.
    if data[0] != 0xE1:
        raise RpzaError("bad RPZA chunk header 0x%02x" % data[0])
    chunk_len = (data[1] << 16) | (data[2] << 8) | data[3]
    if chunk_len > len(data):
        raise RpzaError("chunk length runs past the frame")
    r.p = 4
    total = (w // 4) * (h // 4)
    nblocks = 0
    row = col = 0

    def advance():
        nonlocal row, col, nblocks
        nblocks += 1
        col += 4
        if col + 4 > w:
            col = 0
            row += 4

    def put(y, x, v):
        yy, xx = row + y, col + x
        if yy < h and xx < w:
            pic[yy * stride + xx] = v

    def room():
        # Bytes still available inside this chunk (never read its padding).
        return chunk_len - r.p

    while nblocks < total and room() > 0:
        op = r.byte()
        kind, n = op & 0xE0, op & 0x1F
        if n == 0:
            n = 32
        if kind == 0x80:
            # n blocks of black.
            for _ in range(n):
                for y in range(4):
                    for x in range(4):
                        put(y, x, 0)
                advance()
        elif kind == 0xA0:
            if room() < 2:
                raise RpzaError("fill colour is truncated")
            colour_a = r.be16()
            for _ in range(n):
                for y in range(4):
                    for x in range(4):
                        put(y, x, colour_a)
                advance()
        elif kind == 0xC0:
            # A run of four-colour blocks: two palette colours, then four
            # index bytes per block. A run cut off by the chunk boundary
            # decodes only the complete blocks; the rest are skipped so the
            # block position still advances and their pixels stay put.
            if room() < 4:
                raise RpzaError("four-colour palette is truncated")
            colour_a = r.be16()
            c4 = _blend(colour_a, r.be16())
            complete = min(n, room() // 4)
            for _ in range(complete):
                for y in range(4):
                    idx = r.byte()
                    for x in range(4):
                        # High pair first: pixels run left to right.
                        put(y, x, c4[idx >> (2 * (3 - x)) & 3])
                advance()
            for _ in range(n - complete):
                advance()
            if nblocks >= total or room() == 0:
                break
        elif kind == 0x20:
            # One 16-colour block: every pixel carries its own RGB555 value.
            # The low five bits are part of the opcode, not a repeat count.
            # Only the pixels that actually fit in the chunk are written;
            # the rest keep whatever the previous frame left.
            written = 0
            while written < 16 and room() >= 2:
                y, x = divmod(written, 4)
                put(y, x, r.be16())
                written += 1
            advance()
        elif kind == 0x60:
            # Skip n blocks, leaving whatever the previous frame drew.
            for _ in range(n):
                advance()
        else:
            raise RpzaError("unknown opcode 0x%02x" % op)
    # Frames may stop before every block: the blocks never touched keep what
    # the previous frame drew, which is how unchanged areas stay put.



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
