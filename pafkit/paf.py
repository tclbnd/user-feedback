"""Packed Animation File (PAF) video frames: four frame pages, block copies with mask opcodes, raw, page copy and RLE frames."""
import struct
import zlib

SEQ = [[], [2], [5, 7], [5], [6], [5, 7, 5, 7], [5, 7, 5], [5, 7, 6], [5, 5], [3], [6, 6], [2, 4], [2, 4, 5, 7], [2, 4, 5], [2, 4, 6], [2, 4, 5, 7, 5, 7]]


class PafError(ValueError):
    pass


class In:
    def __init__(self, d):
        self.d, self.p = d, 0

    def left(self):
        return len(self.d) - self.p

    def byte(self):
        v = self.d[self.p] if self.p < len(self.d) else 0
        self.p += 1
        return v

    def be16(self):
        return self.byte() << 8 | self.byte()

    def le16(self):
        return self.byte() | self.byte() << 8

    def take(self, n):
        b = self.d[self.p:self.p + n]
        self.p += n
        return b + bytes(n - len(b))


class Paf:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.fsize = w * ((h + 255) // 256 * 256)
        self.vsize = w * h
        self.pages = [bytearray(self.fsize) for _ in range(4)]
        self.cur = 0
        self.pal = [(0, 0, 0)] * 256

    def src_pos(self, g):
        v = g.be16()
        page, x, y = v >> 14, (v & 0x7F) * 2, ((v >> 7) & 0x7F) * 2
        return page, x + y * self.w

    def decode0(self, g, code):
        w = self.w
        n = g.byte()
        if n:
            for _ in range(n):
                v = g.be16()
                page, x, y = v >> 14, (v & 0x7F) * 2, ((v >> 7) & 0x7F) * 2
                dst = x + y * w
                offset = x // 4
                j = g.le16() + offset
                if g.left() < (j - offset) * 16:
                    raise PafError("block data is truncated")
                buf = self.pages[page]
                while True:
                    offset += 1
                    if dst + 3 * w + 4 > self.fsize:
                        raise PafError("block goes past the page")
                    for r in range(4):
                        buf[dst + r * w:dst + r * w + 4] = g.take(4)
                    if offset & 0x3F == 0:
                        dst += w * 3
                    dst += 4
                    if offset >= j:
                        break
        cur = self.pages[self.cur]
        dst, i = 0, 0
        while True:
            page, s = self.src_pos(g)
            if s + 3 * w + 4 > self.fsize or dst + 3 * w + 4 > self.fsize or g.left() < 4:
                raise PafError("block copy goes past the page")
            src = self.pages[page]
            for r in range(4):
                cur[dst + r * w:dst + r * w + 4] = bytes(src[s + r * w:s + r * w + 4])
            i += 1
            if i & 0x3F == 0:
                dst += w * 3
            dst += 4
            if i >= self.vsize // 16:
                break
        osize = g.le16()
        g.p += 2
        if g.left() < osize:
            raise PafError("opcodes are truncated")
        ops = g.d[g.p:]
        g.p += osize
        op = 0
        dst = 0
        color = mask = 0
        spage, s = 0, 0
        for by in range(0, self.h, 4):
            for bx in range(0, w, 4):
                if op > osize:
                    raise PafError("opcodes ran out")
                o = ops[op] if op < len(ops) else 0
                if bx & 4:
                    opc = o & 15
                    op += 1
                else:
                    opc = o >> 4
                for c in SEQ[opc]:
                    off = 0 if c in (2, 5) else 2 * w
                    if c in (2, 3, 4):
                        color = g.byte()
                        mask = g.byte()
                        for k in range(4):
                            if mask & (1 << (7 - k)):
                                cur[dst + off + k] = color
                            if mask & (1 << (3 - k)):
                                cur[dst + off + w + k] = color
                    else:
                        spage, s = self.src_pos(g)
                        if s + off + w + 4 > self.fsize:
                            raise PafError("mask copy goes past the page")
                        mask = g.byte()
                        src = self.pages[spage]
                        for k in range(4):
                            if mask & (1 << (7 - k)):
                                cur[dst + off + k] = src[s + off + k]
                            if mask & (1 << (3 - k)):
                                cur[dst + off + w + k] = src[s + off + w + k]
                dst += 4
            dst += w * 3

    def frame(self, pkt):
        if len(pkt) < 2:
            raise PafError("packet is too short")
        g = In(pkt)
        code = g.byte()
        kind = code & 15
        if kind > 4:
            raise PafError("unknown frame type")
        if kind == 0 and self.vsize // 32 - g.left() > self.vsize // 32 * 95 // 100:
            raise PafError("frame data is too short")
        if code & 0x20:
            self.cur = 0
        if code & 0x40:
            idx, cnt = g.byte(), g.byte() + 1
            if g.left() < 3 * cnt:
                raise PafError("bad palette update")
            for k in range(min(cnt, 256 - idx)):
                r, gg, b = g.byte(), g.byte(), g.byte()
                self.pal[idx + k] = ((r << 2) | (r >> 4), (gg << 2) | (gg >> 4), (b << 2) | (b >> 4))
        cur = self.pages[self.cur]
        if kind == 0:
            self.decode0(g, code)
        elif kind == 1:
            if g.left() < self.vsize:
                raise PafError("raw frame is truncated")
            cur[:self.vsize] = g.take(self.vsize)
        elif kind == 2:
            ref = g.byte() & 3
            cur[:] = self.pages[ref]
        else:
            g.p += 2
            d = 0
            while d < self.vsize:
                if g.left() < 2:
                    raise PafError("rle data ran out")
                c = g.byte()
                c = c - 256 if c & 0x80 else c
                n = abs(c) + 1
                if d + n > self.vsize:
                    raise PafError("rle run goes past the frame")
                if c < 0:
                    cur[d:d + n] = bytes([g.byte()]) * n
                else:
                    cur[d:d + n] = g.take(n)
                d += n
        img = b"".join(bytes(self.pal[v]) for v in cur[:self.vsize])
        self.cur = (self.cur + 1) & 3
        return img


def parse(data):
    if data[:8] != b"PAFPACK1":
        raise PafError("not a PAF pack")
    w, h, count = struct.unpack_from("<HHH", data, 8)
    if w != 256 or not h or h & 3:
        raise PafError("unsupported picture size")
    v = Paf(w, h)
    pos = 14
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise PafError("frame table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        pkt = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(pkt) < n:
            raise PafError("frame is truncated")
        try:
            img = v.frame(pkt)
        except PafError:
            frames.append({"state": "error", "crc32": None})
            continue
        frames.append({"state": "decoded", "crc32": "%08x" % zlib.crc32(img)})
    return {"width": w, "height": h, "frames": frames}
