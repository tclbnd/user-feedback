"""American Laser Games MM video packets: palette, run-length intra frames and masked inter frames, with half-resolution variants."""
import struct
import zlib

KINDS = {0x08: (0, 0, 0), 0x0C: (0, 1, 0), 0x0E: (0, 1, 1), 0x05: (1, 0, 0), 0x0D: (1, 1, 0), 0x0F: (1, 1, 1)}


class MmvError(ValueError):
    pass


class In:
    def __init__(self, d, p=0, end=None):
        self.d, self.p = d, p
        self.end = len(d) if end is None else end

    def left(self):
        return self.end - self.p

    def byte(self):
        v = self.d[self.p] if self.p < self.end else 0
        self.p += 1
        return v


class Mmv:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.pix = bytearray(w * h)
        self.pal = [0] * 256

    def palette(self, g):
        g.p += 4
        for i in range(128):
            v = g.byte() << 16 | g.byte() << 8 | g.byte()
            self.pal[i] = v
            self.pal[i + 128] = (v << 2) & 0xFFFFFF

    def intra(self, g, hh, hv):
        x = y = 0
        while g.left() > 0:
            if y >= self.h:
                return
            c = g.byte()
            if c & 0x80:
                run = 1
            else:
                run = (c & 0x7F) + 2
                c = g.byte()
            run *= 1 + hh
            run = min(run, self.w - x)
            for dy in range(1 + hv):
                if y + dy >= self.h:
                    break
                o = (y + dy) * self.w + x
                self.pix[o:o + run] = bytes([c]) * run
            x += run
            if x >= self.w:
                x = 0
                y += 1 + hv

    def inter(self, g, hh, hv):
        off = g.byte() | g.byte() << 8
        if g.left() < off:
            raise MmvError("data offset is past the packet")
        data = In(g.d, g.p + off, g.end)
        stop = g.p + off
        y = 0
        while g.p < stop:
            length = g.byte()
            x = g.byte() + ((length & 0x80) << 1)
            length &= 0x7F
            if not length:
                y += 1
                continue
            if y + hv >= self.h:
                return
            for _ in range(length):
                mask = g.byte()
                for j in range(8):
                    if x + hh >= self.w:
                        break
                    if mask >> j & 1:
                        c = data.byte()
                        for dy in range(1 + hv):
                            for dx in range(1 + hh):
                                self.pix[(y + dy) * self.w + x + dx] = c
                    x += 1 + hh
            y += 1 + hv


def parse(data):
    if data[:8] != b"MMPACK01":
        raise MmvError("not an MM pack")
    w, h, count = struct.unpack_from("<HHH", data, 8)
    if not w or not h or w & 1 or h & 1:
        raise MmvError("bad picture size")
    v = Mmv(w, h)
    pos = 14
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise MmvError("packet table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        pkt = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(pkt) < n:
            raise MmvError("packet is truncated")
        if n < 6:
            frames.append({"state": "error", "crc32": None})
            continue
        kind = pkt[0] | pkt[1] << 8
        g = In(pkt, 6)
        try:
            if kind == 0x31:
                v.palette(g)
                frames.append({"state": "palette"})
                continue
            inter, hh, hv = KINDS.get(kind, (0, 0, 0))
            (v.inter if inter else v.intra)(g, hh, hv)
        except MmvError:
            frames.append({"state": "error", "crc32": None})
            continue
        img = b"".join(bytes((v.pal[p] >> 16, v.pal[p] >> 8 & 0xFF, v.pal[p] & 0xFF)) for p in v.pix)
        frames.append({"state": "decoded", "crc32": "%08x" % zlib.crc32(img)})
    return {"width": w, "height": h, "frames": frames}
