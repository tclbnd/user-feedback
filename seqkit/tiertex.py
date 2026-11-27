"""Tiertex SEQ video frames (.tsq): decode each frame to a 256 x 128 palette picture."""
import zlib

MAGIC = b"TSEQVID\0"
W, H = 256, 128


class SeqError(ValueError):
    pass


class Bits:
    def __init__(self, data, start=0):
        self.d, self.bit = data, start * 8

    def left(self, end):
        return end * 8 - self.bit

    def get(self, n):
        v = 0
        for _ in range(n):
            byte = self.d[self.bit >> 3] if (self.bit >> 3) < len(self.d) else 0
            v = v << 1 | (byte >> (7 - (self.bit & 7)) & 1)
            self.bit += 1
        return v

    def sget(self, n):
        v = self.get(n)
        return -v if v & (1 << (n - 1)) else v


def rle_block(d, p, end):
    b = Bits(d, p)
    codes, sz = [], 0
    while len(codes) < 64 and sz < 64:
        if b.left(end) < 4:
            raise SeqError("RLE code table runs out")
        c = b.sget(4)
        codes.append(c)
        sz += abs(c)
    p += (b.bit - p * 8 + 7) // 8
    out = bytearray()
    left = 64
    for c in codes:
        if left <= 0:
            break
        if c < 0:
            if end - p < 1:
                raise SeqError("RLE data runs out")
            n = min(-c, left)
            out += bytes([d[p]]) * n
            p += 1
        else:
            if end - p < c:
                raise SeqError("RLE data runs out")
            n = min(c, left)
            out += d[p:p + n]
            p += c
        left -= n
    if left > 0:
        raise SeqError("RLE block is short")
    return bytes(out), p


class Decoder:
    def __init__(self):
        self.pic = bytearray(W * H)
        self.pal = [0] * 256

    def put(self, bx, by, x, y, v):
        self.pic[(by + y) * W + bx + x] = v

    def frame(self, d):
        if not d:
            raise SeqError("empty frame")
        end = len(d)
        flags, p = d[0], 1
        if flags & 1:
            if end - p < 768:
                raise SeqError("palette is truncated")
            for k in range(256):
                c = [d[p + j] << 2 & 0xFF for j in range(3)]
                self.pal[k] = c[0] << 16 | c[1] << 8 | c[2]
                p += 3
        if flags & 2:
            if end - p < 128:
                raise SeqError("block map is truncated")
            ops = Bits(d, p)
            p += 128
            for by in range(0, H, 8):
                for bx in range(0, W, 8):
                    op = ops.get(1) | ops.get(1) << 1
                    if op == 1:
                        if end - p < 1:
                            raise SeqError("block data runs out")
                        n = d[p]
                        p += 1
                        if n & 0x80:
                            if n & 3 == 1:
                                blk, p = rle_block(d, p, end)
                                for y in range(8):
                                    for x in range(8):
                                        self.put(bx, by, x, y, blk[y * 8 + x])
                            else:
                                blk, p = rle_block(d, p, end)
                                for y in range(8):
                                    for x in range(8):
                                        self.put(bx, by, x, y, blk[y * 8 + x])
                        else:
                            if n <= 0:
                                raise SeqError("empty colour table")
                            bits = n.bit_length()
                            if end - p < n + 8 * bits:
                                raise SeqError("colour block runs out")
                            ts = p
                            p += n
                            g = Bits(d, p)
                            p += bits * 8
                            for y in range(8):
                                for x in range(8):
                                    idx = g.get(bits)
                                    self.put(bx, by, x, y, d[ts + idx] if ts + idx < end else 0)
                    elif op == 2:
                        if end - p < 64:
                            raise SeqError("raw block runs out")
                        for y in range(8):
                            for x in range(8):
                                self.put(bx, by, x, y, d[p + y * 8 + x])
                        p += 64
                    elif op == 3:
                        while True:
                            if end - p < 2:
                                raise SeqError("pixel list runs out")
                            pos, v = d[p], d[p + 1]
                            p += 2
                            self.put(bx, by, pos >> 3 & 7, pos & 7, v)
                            if pos & 0x80:
                                break


def parse(data):
    if len(data) < 12 or data[:8] != MAGIC:
        raise SeqError("not a Tiertex SEQ frame file")
    count = int.from_bytes(data[8:12], "little")
    dec = Decoder()
    pos, frames = 12, []
    for _ in range(count):
        if pos + 4 > len(data):
            raise SeqError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise SeqError("frame data is truncated")
        pos += 4 + n
        try:
            dec.frame(body)
            ok = True
        except SeqError:
            ok = False
        frames.append({"ok": ok, "crc32": "%08x" % zlib.crc32(bytes(dec.pic))})
    rgb = b"".join(dec.pal[v].to_bytes(3, "big") for v in dec.pic)
    return {"frames": frames, "colours": len(set(dec.pic)), "rgb_crc32": "%08x" % zlib.crc32(rgb)}
