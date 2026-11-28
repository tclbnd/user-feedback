"""Gryphon ARBC screen video: colour segments painting masked tiles at five block sizes over the previous frame."""
import struct
import zlib

SIZES = ((0x01, 4), (0x02, 16), (0x04, 64), (0x08, 256), (0x10, 1024))


class ArbcError(ValueError):
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

    def le16(self):
        return self.byte() | self.byte() << 8


class Arbc:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.prev = None

    def tiles(self, g, pix, size, color):
        w, h = self.w, self.h
        n = g.le16()
        step = 1 if size == 4 else 4
        for _ in range(n):
            ty, tx = g.byte(), g.byte()
            mask = g.le16()
            sy, sx = ty * size, tx * size
            if sx + size > w or sy + size > h:
                continue
            for j in range(sy, sy + size, step):
                for k in range(sx, sx + size, step):
                    if mask & 1:
                        for m in range(step):
                            for q in range(step):
                                if j + m < h and k + q < w:
                                    pix[(j + m) * w + k + q] = color
                    mask >>= 1

    def packet(self, pkt):
        if len(pkt) < 8:
            raise ArbcError("packet is too short")
        g = In(pkt, 8)
        n = g.le16()
        pix = [(0, 0, 0)] * (self.w * self.h)
        for _ in range(n):
            r, gg, b = g.byte(), g.byte(), g.byte()
            g.p += 3
            flag = g.byte()
            for bit, size in SIZES:
                if flag & bit:
                    self.tiles(g, pix, size, (r, gg, b))
        self.prev = pix
        return pix


def parse(data):
    if data[:8] != b"ARBCPACK":
        raise ArbcError("not an ARBC pack")
    w, h, count = struct.unpack_from("<HHH", data, 8)
    if not w or not h:
        raise ArbcError("empty picture")
    v = Arbc(w, h)
    pos = 14
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise ArbcError("packet table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        pkt = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(pkt) < n:
            raise ArbcError("packet is truncated")
        try:
            pix = v.packet(pkt)
        except ArbcError:
            frames.append({"state": "error", "crc32": None})
            continue
        if pix is None:
            frames.append({"state": "skipped"})
            continue
        frames.append({"state": "decoded", "crc32": "%08x" % zlib.crc32(b"".join(bytes(p) for p in pix))})
    return {"width": w, "height": h, "frames": frames}
