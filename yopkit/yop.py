"""Psygnosis YOP video (.ysq): decode the frames of one movie into palette pictures."""
import zlib

MAGIC = b"YOPSEQ\0\0"
PAINT = ((1, 2, 3, 4), (1, 2, 0, 3), (1, 2, 1, 3), (1, 2, 2, 3), (1, 0, 2, 3), (1, 0, 0, 2), (1, 0, 1, 2), (1, 1, 2, 3),
         (0, 1, 2, 3), (0, 1, 0, 2), (1, 1, 0, 2), (0, 1, 1, 2), (0, 0, 1, 2), (0, 0, 0, 1), (1, 1, 1, 2))


class YopError(ValueError):
    pass


class Decoder:
    def __init__(self, w, h, ncol, first):
        self.w, self.h, self.ncol, self.first = w, h, ncol, first
        self.pic = bytearray(w * h)
        self.pal = [0] * 256

    def frame(self, pkt):
        if len(pkt) < 4 + 3 * self.ncol:
            raise YopError("frame is too short")
        odd = pkt[0]
        if odd > 1:
            raise YopError("bad odd/even flag")
        src = 4
        base = self.first[0]
        for i in range(self.ncol):
            r, g, b = pkt[src:src + 3]
            self.pal[base + i] = (r << 2 & 0xFF) << 16 | (g << 2 & 0xFF) << 8 | (b << 2 & 0xFF)
            src += 3
        low = None
        w = self.w
        pos = 0
        for y in range(0, self.h, 2):
            for x in range(0, w, 2):
                if src >= len(pkt):
                    raise YopError("frame data runs out")
                if low is not None:
                    tag, low = pkt[low] >> 4, None
                else:
                    low = src
                    tag = pkt[src] & 0x0F
                    src += 1
                dst = y * w + x
                if tag != 15:
                    lut = PAINT[tag]
                    if len(pkt) - src < lut[3]:
                        raise YopError("frame data runs out")
                    rd = lambda k: pkt[src + k] if src + k < len(pkt) else 0
                    self.pic[dst] = rd(0)
                    self.pic[dst + 1] = rd(lut[1])
                    self.pic[dst + w] = rd(lut[0])
                    self.pic[dst + w + 1] = rd(lut[2])
                    src += lut[3]
                else:
                    pass


def parse(data):
    if len(data) < 19 or data[:8] != MAGIC:
        raise YopError("not a YOP frame sequence")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    ncol, f0, f1 = data[12], data[13], data[14]
    count = int.from_bytes(data[15:19], "little")
    if not w or not h or w & 1 or h & 1:
        raise YopError("width and height must be non-zero and even")
    if ncol + f0 > 256 or ncol + f1 > 256:
        raise YopError("palette parameters do not fit")
    dec = Decoder(w, h, ncol, (f0, f1))
    pos, frames = 19, []
    for _ in range(count):
        if pos + 4 > len(data):
            raise YopError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise YopError("frame data is truncated")
        pos += 4 + n
        try:
            dec.frame(body)
            ok = True
        except YopError:
            ok = False
        frames.append({"ok": ok, "crc32": "%08x" % zlib.crc32(bytes(dec.pic))})
    rgb = b"".join(dec.pal[v].to_bytes(3, "big") for v in dec.pic)
    return {"width": w, "height": h, "frames": frames, "colours": len(set(dec.pic)), "rgb_crc32": "%08x" % zlib.crc32(rgb)}
