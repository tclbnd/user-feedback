"""Beam Software VB movies (.vb): 4 x 4 block frames with motion, fills, patterns and palette updates."""
import zlib

PATTERNS = (
    0x0660, 0xFF00, 0xCCCC, 0xF000, 0x8888, 0x000F, 0x1111, 0xFEC8,
    0x8CEF, 0x137F, 0xF731, 0xC800, 0x008C, 0x0013, 0x3100, 0xCC00,
    0x00CC, 0x0033, 0x3300, 0x0FF0, 0x6666, 0x00F0, 0x0F00, 0x2222,
    0x4444, 0xF600, 0x8CC8, 0x006F, 0x1331, 0x318C, 0xC813, 0x33CC,
    0x6600, 0x0CC0, 0x0066, 0x0330, 0xF900, 0xC88C, 0x009F, 0x3113,
    0x6000, 0x0880, 0x0006, 0x0110, 0xCC88, 0xFC00, 0x00CF, 0x88CC,
    0x003F, 0x1133, 0x3311, 0xF300, 0x6FF6, 0x0603, 0x08C6, 0x8C63,
    0xC631, 0x6310, 0xC060, 0x0136, 0x136C, 0x36C8, 0x6C80, 0x324C)


class VbError(ValueError):
    pass


class Bytes:
    def __init__(self, d, p=0):
        self.d, self.p = d, p

    def left(self):
        return max(0, len(self.d) - self.p)

    def u8(self):
        v = self.d[self.p] if self.p < len(self.d) else 0
        self.p += 1
        return v

    def u16(self):
        return self.u8() | self.u8() << 8

    def s16(self):
        v = self.u16()
        return v - 0x10000 if v & 0x8000 else v

    def u32(self):
        return self.u16() | self.u16() << 16


class Decoder:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.frame = bytearray(w * h)
        self.prev = bytearray(w * h)
        self.pal = [(0, 0, 0)] * 256

    def line_ok(self, pos):
        return 0 <= pos and pos + 4 <= len(self.prev)

    def framedata(self, g, offset):
        w = self.w
        cur, prev = 0, offset
        blocks = (w >> 2) * (self.h >> 2)
        col = 0
        types = 0
        for blk in range(blocks):
            if not blk & 3:
                if g.left() < 1:
                    return
                types = g.u8()
            kind = (types & 0x03) << 6
            if kind == 0x00:
                for y in range(4):
                    s = prev + y * w
                    self.frame[cur + y * w:cur + y * w + 4] = self.prev[s:s + 4].ljust(4, b"\0")
            elif kind == 0x40:
                t = g.u8()
                if not t:
                    if g.left() < 16:
                        return
                    for y in range(4):
                        self.frame[cur + y * w:cur + y * w + 4] = bytes(g.u8() for _ in range(4))
                else:
                    x = t & 0xF
                    yv = t >> 4
                    d = x + yv * w
                    for y in range(4):
                        s = prev + d + y * w
                        self.frame[cur + y * w:cur + y * w + 4] = self.prev[s:s + 4] if self.line_ok(s) else bytes(4)
            elif kind == 0x80:
                v = g.u8()
                for y in range(4):
                    self.frame[cur + y * w:cur + y * w + 4] = bytes([v]) * 4
            else:
                t = g.u8()
                ptype, pat = t >> 6, PATTERNS[t & 0x3F]
                if ptype == 0:
                    a, b = g.u8(), g.u8()
                    for y in range(4):
                        for x in range(4):
                            self.frame[cur + x + y * w] = b if pat & 0x8000 else a
                            pat = (pat << 1) & 0xFFFF
                elif ptype in (1, 2):
                    if ptype == 1:
                        pat = ~pat & 0xFFFF
                    a = g.u8()
                    for y in range(4):
                        for x in range(4):
                            s = prev + x + y * w
                            if pat & 1 and 0 <= s < len(self.prev):
                                self.frame[cur + x + y * w] = self.prev[s]
                            else:
                                self.frame[cur + x + y * w] = a
                            pat >>= 1
                else:
                    return
            types >>= 2
            cur += 4
            prev += 4
            col += 1
            if col == w >> 2:
                col = 0
                cur += w * 3
                prev += w * 3

    def palette(self, g, size):
        start = g.u8()
        n = g.u8() - 1
        for i in range(start, min(256, start + n)):
            self.pal[i] = (g.u8(), g.u8(), g.u8())

    def packet(self, pkt):
        if len(pkt) < 2:
            raise VbError("packet is too short")
        g = Bytes(pkt)
        flags = g.u16()
        offset = 0
        if flags & 0x01:
            i, j = g.s16(), g.s16()
            offset = 0
        if flags & 0x08:
            size = g.u32()
            if size > g.left() + 4 or size < 4:
                raise VbError("video chunk size is invalid")
            self.framedata(Bytes(pkt, g.p), offset)
            g.p += size - 4
        if flags & 0x10:
            size = g.u32()
            self.palette(g, size)
        out = bytes(self.frame)
        self.frame, self.prev = self.prev, self.frame
        return out


def parse(data):
    if len(data) < 14 or data[:8] != b"VBMOVIE\0":
        raise VbError("not a VB movie")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    count = int.from_bytes(data[12:14], "little")
    if not w or not h or w % 4 or h % 4:
        raise VbError("width and height must be non-zero multiples of 4")
    dec = Decoder(w, h)
    pos = 14
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise VbError("packet table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        pkt = data[pos + 4:pos + 4 + n]
        if len(pkt) < n:
            raise VbError("packet is truncated")
        pos += 4 + n
        try:
            pic = dec.packet(pkt)
            rgb = b"".join(bytes(dec.pal[v]) for v in pic)
            frames.append({"ok": True, "crc32": "%08x" % zlib.crc32(rgb)})
        except VbError:
            frames.append({"ok": False, "crc32": None})
    return {"width": w, "height": h, "frames": frames}
