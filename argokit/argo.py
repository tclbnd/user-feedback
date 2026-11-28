"""Argonaut BRP video chunks (8-bit): PAL8, MAD1 block types and RLE frames."""
import struct
import zlib


class ArgoError(ValueError):
    pass


class In:
    def __init__(self, d, p=0):
        self.d, self.p = d, p

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


class Argo:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.pix = bytearray(w * h)
        self.pal = [0] * 256

    def pal8(self, g):
        start, count = g.le16(), g.le16()
        if g.left() < 3 * count:
            raise ArgoError("bad palette chunk")
        for i in range(min(count, 256 - start)):
            self.pal[start + i] = g.byte() << 16 | g.byte() << 8 | g.byte()

    def mad1(self, g):
        w, h, P = self.w, self.h, self.pix
        while g.left() > 0:
            t = g.byte()
            if t == 0xFF:
                break
            if t == 8:
                for y in range(0, h, 8):
                    for x in range(0, w, 8):
                        f = g.byte()
                        for by in range(8):
                            if y + by < h:
                                o = (y + by) * w + x
                                P[o:o + min(8, w - x)] = bytes([f]) * min(8, w - x)
            elif t == 7:
                while g.left() > 0:
                    bsize = g.byte()
                    if not bsize:
                        break
                    count = g.be16()
                    while count > 0:
                        if g.left() < 4:
                            raise ArgoError("motion data is truncated")
                        mvx, mvy = g.byte() * bsize, g.byte() * bsize
                        a, b = g.byte(), g.byte()
                        c = ((a & 0x3F) << 8) + b
                        mx, my = mvx + (c & 0x7F), mvy + (c >> 7)
                        mx, my = min(max(mx, 0), w - bsize), min(max(my, 0), h - bsize)
                        bw = bh = bsize
                        for r in range(bh):
                            s = (my + r) * w + mx
                            d = (mvy + r) * w + mvx
                            P[d:d + bw] = bytes(P[s:s + bw])
                        count -= 1
            elif t == 6:
                if g.left() < w * h:
                    raise ArgoError("raw picture is truncated")
                P[:] = g.d[g.p:g.p + w * h]
                g.p += w * h
            elif t == 5:
                for y in range(0, h, 2):
                    for x in range(0, w, 2):
                        f = g.byte()
                        o = y * w + x
                        P[o:o + 2] = bytes([f, f])
            elif t == 3:
                g.p += 2
            elif t == 2:
                pos = dy = 0
                while g.left() > 0:
                    cnt = g.byte()
                    skip = cnt & 0x3F
                    cnt >>= 6
                    pos += skip
                    while pos >= w:
                        pos -= w
                        dy += 1
                        if dy >= h:
                            return
                    while cnt >= 0:
                        bits = g.byte()
                        for _ in range(4):
                            code = bits >> 6
                            o = dy * w + pos
                            if code == 1:
                                P[o] = P[o - 1] if o else 0
                            elif code == 2:
                                if dy < 1:
                                    raise ArgoError("no pixel above")
                                P[o] = P[o - w]
                            elif code == 3:
                                P[o] = g.byte()
                            pos += 1
                            if pos >= w:
                                pos -= w
                                dy += 1
                                if dy >= h:
                                    return
                            bits = (bits << 2) & 0xFF
                        cnt -= 1
            else:
                raise ArgoError("unknown block type")

    def rle(self, g):
        w, h, P = self.w, self.h, self.pix
        pos = y = 0
        while g.left() > 0:
            cnt, px = g.byte(), g.byte()
            while cnt > 0:
                P[pos + y * w] = px
                cnt -= 1
                pos += 1
                if pos >= w:
                    pos = 0
                    y += 1
                    if y >= h:
                        return


def parse(data):
    if data[:8] != b"ARGOPACK":
        raise ArgoError("not a BRP video pack")
    w, h, count = struct.unpack_from("<HHH", data, 8)
    if not w or not h or w & 1 or h & 1:
        raise ArgoError("bad picture size")
    v = Argo(w, h)
    pos = 14
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise ArgoError("chunk table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        pkt = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(pkt) < n:
            raise ArgoError("chunk is truncated")
        if n < 4:
            frames.append({"state": "error", "crc32": None})
            continue
        tag = pkt[:4]
        g = In(pkt, 4)
        try:
            if tag == b"PAL8":
                v.pal8(g)
                frames.append({"state": "palette"})
                continue
            if tag == b"MAD1":
                v.mad1(g)
            elif tag in (b"AVCF", b"ALCD"):
                raise ArgoError("block map frames are not supported")
            elif tag in (b"RLEF", b"RLED"):
                v.rle(g)
            else:
                raise ArgoError("unknown chunk")
        except ArgoError:
            frames.append({"state": "error", "crc32": None})
            continue
        img = b"".join(bytes((v.pal[p] >> 16, v.pal[p] >> 8 & 0xFF, v.pal[p] & 0xFF)) for p in v.pix)
        frames.append({"state": "decoded", "crc32": "%08x" % zlib.crc32(img)})
    return {"width": w, "height": h, "frames": frames}
