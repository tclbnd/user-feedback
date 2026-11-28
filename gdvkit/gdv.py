"""Gremlin Digital Video frames from a rip pack: palette frames and the LZ modes 2, 5, 6 and 8 over a 4096-byte preamble."""
import struct
import zlib

PRE = 4096


class GdvError(ValueError):
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

    def le32(self):
        return self.le16() | self.le16() << 16


class Bits2:
    """2-bit tags, a new tag byte is read when the previous one is used up."""
    def __init__(self, g):
        self.g, self.q, self.fill = g, 0, 0

    def get(self):
        if not self.fill:
            self.q |= self.g.byte()
            self.fill = 8
        r = self.q & 3
        self.q >>= 2
        self.fill -= 2
        return r


class Bits32:
    """Little-endian bit queue: starts with a 32-bit word, low bits first, refilled with a 16-bit word whenever 16 or fewer bits remain."""
    def __init__(self, g):
        self.g = g
        self.q = g.le32()
        self.fill = 32

    def get(self, n):
        r = self.q & ((1 << n) - 1)
        self.q >>= n
        self.fill -= n
        if self.fill <= 16:
            self.q |= self.g.le16() << self.fill
            self.fill += 16
        return r


class Gdv:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.buf = bytearray(PRE + w * h)
        self.pal = [(0, 0, 0)] * 256

    def copy(self, pos, off, n):
        src = pos - abs(off)
        n = min(n, len(self.buf) - pos)
        self.buf[pos:pos + n] = self.buf[src:src + n]
        return pos + n

    def put(self, pos, v):
        if pos < len(self.buf):
            self.buf[pos] = v
        return pos + 1

    def mode2(self, g):
        bits = Bits2(g)
        pos = PRE
        while pos < len(self.buf) and g.left() > 0:
            tag = bits.get()
            if tag == 0:
                pos = self.put(pos, g.byte())
            elif tag == 1:
                b = g.byte()
                n, top = (b & 15) + 3, b >> 4
                off = (g.byte() << 4) + top - 4095
                pos = self.copy(pos, off, n)
            elif tag == 2:
                pos += g.byte() + 2
            else:
                break

    def mode5(self, g, skip):
        bits = Bits2(g)
        pos = PRE + skip
        while pos < len(self.buf) and g.left() > 0:
            tag = bits.get()
            if g.left() < 1:
                raise GdvError("frame data ran out")
            if tag == 0:
                pos = self.put(pos, g.byte())
            elif tag == 1:
                b = g.byte()
                n, top = (b & 15) + 3, b >> 4
                off = (g.byte() << 4) + top - 4095
                pos = self.copy(pos, off, n)
            elif tag == 2:
                pos += g.byte()
            else:
                b = g.byte()
                pos = self.copy(pos, -(b >> 2) - 1, (b & 3) + 2)

    def mode68(self, g, skip, use8):
        bits = Bits32(g)
        pos = PRE + skip
        while pos < len(self.buf) and g.left() > 0:
            tag = bits.get(2)
            if tag == 0:
                if bits.get(1) == 0:
                    pos = self.put(pos, g.byte())
                else:
                    n, lb = 2, 0
                    while True:
                        lb += 1
                        v = bits.get(lb)
                        n += v
                        if v != (1 << lb) - 1:
                            break
                        if lb >= 16:
                            raise GdvError("bad literal length")
                    for _ in range(n):
                        pos = self.put(pos, g.byte())
            elif tag == 1:
                if bits.get(1) == 0:
                    n = bits.get(4) + 2
                else:
                    bb = g.byte()
                    n = bb + 18
                pos += n
            elif tag == 2:
                sub = bits.get(2)
                if sub != 3:
                    offs = (bits.get(4) << 8) + g.byte()
                    pos = self.copy(pos, offs - 4096, sub + 3)
                else:
                    b = g.byte()
                    pos = self.copy(pos, -((b & 0x7F) + 1), 2 if not b & 0x80 else 3)
            else:
                if use8:
                    b = g.byte()
                    if b & 0xC0 == 0xC0:
                        n = (b & 0x3F) + 8
                        q = bits.get(4)
                        off = (q << 8) + g.byte() + 1
                    else:
                        if not b & 0x80:
                            n, o1 = (b >> 4) + 6, b & 15
                        else:
                            n, o1 = (b & 0x3F) + 14, bits.get(4)
                        off = (o1 << 8) + g.byte() - 4096
                else:
                    b = g.byte()
                    n = (b >> 4) + 6
                    off = ((b & 15) << 8) + g.byte() - 4096
                pos = self.copy(pos, off, n)

    def frame(self, pkt):
        g = In(pkt)
        flags = g.le32()
        comp = flags & 15
        if flags & 0x30:
            raise GdvError("scaled frames are not supported")
        if comp < 2 and g.left() < 768:
            raise GdvError("palette is truncated")
        if comp < 2:
            self.pal = [(g.byte(), g.byte(), g.byte()) for _ in range(256)]
        elif comp == 2:
            self.mode2(g)
        elif comp == 5:
            self.mode5(g, 0)
        elif comp in (6, 8):
            self.mode68(g, 0, comp == 8)


def parse(data):
    if data[:8] != b"GDVPACK1":
        raise GdvError("not a GDV pack")
    w, h, count = struct.unpack_from("<HHH", data, 8)
    if not w or not h:
        raise GdvError("empty picture")
    v = Gdv(w, h)
    pos = 14
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise GdvError("frame table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        pkt = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(pkt) < n:
            raise GdvError("frame is truncated")
        try:
            v.frame(pkt)
        except GdvError:
            frames.append({"state": "error", "crc32": None})
            continue
        img = b"".join(bytes(v.pal[p]) for p in v.buf[PRE:])
        frames.append({"state": "decoded", "crc32": "%08x" % zlib.crc32(img)})
    return {"width": w, "height": h, "frames": frames}
