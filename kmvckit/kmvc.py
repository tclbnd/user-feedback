"""Karl Morton's Video Codec (KMVC) frame sequences (.kmv): decode each frame through the palette."""
import zlib

MAGIC = b"KMVCSEQ\0"
BUF = 320 * 200


class KmvcError(ValueError):
    pass


class Stop(Exception):
    pass


class Reader:
    def __init__(self, d):
        self.d, self.p = d, 0

    def left(self):
        return max(0, len(self.d) - self.p)

    def byte(self):
        v = self.d[self.p] if self.p < len(self.d) else 0
        self.p += 1
        return v

    def peek(self):
        return self.d[self.p] if self.p < len(self.d) else 0


class Bits:
    def __init__(self, r):
        self.r = r
        self.bits, self.buf = 8, 0

    def get(self):
        if self.bits == 8:
            self.buf, self.bits = self.r.byte(), 0
        v = self.buf >> self.bits & 1
        self.bits += 1
        return v


def idx(x, y):
    return x + y * 320


def decode_blocks(cur, prev, r, w, h, inter):
    bb = Bits(r)
    for by in range(0, h, 8):
        for bx in range(0, w, 8):
            if not inter and not r.left():
                raise Stop
            res = bb.get()
            if inter and not res:
                if not bb.get():
                    if not r.left():
                        raise Stop
                    v = r.byte()
                    for i in range(64):
                        cur[idx(bx + (i & 7), by + (i >> 3))] = v
                else:
                    for i in range(64):
                        cur[idx(bx + (i & 7), by + (i >> 3))] = prev[idx(bx + (i & 7), by + (i >> 3))]
                continue
            if not inter and not res:
                v = r.byte()
                for i in range(64):
                    cur[idx(bx + (i & 7), by + (i >> 3))] = v
                continue
            if inter and not r.left():
                raise Stop
            for i in range(4):
                x0, y0 = bx + (i & 1) * 4, by + (i & 2) * 2
                if not bb.get():
                    if not bb.get():
                        v = r.byte()
                        for j in range(16):
                            cur[idx(x0 + (j & 3), y0 + (j >> 2))] = v
                    else:
                        v = r.byte()
                        if inter:
                            mx, my = v & 0xF, v >> 4
                            for j in range(16):
                                cur[idx(x0 + (j & 3), y0 + (j >> 2))] = prev[idx(x0 + (j & 3) + mx, y0 + (j >> 2) + my)]
                        else:
                            mx, my = v & 0xF, v >> 4
                            for j in range(16):
                                cur[idx(x0 + (j & 3), y0 + (j >> 2))] = cur[idx(x0 + (j & 3) + mx, y0 + (j >> 2) + my)]
                    continue
                for j in range(4):
                    x1, y1 = x0 + (j & 1) * 2, y0 + (j & 2)
                    if not bb.get():
                        if not bb.get():
                            v = r.byte()
                            for dy in (0, 1):
                                for dx in (0, 1):
                                    cur[idx(x1 + dx, y1 + dy)] = v
                        else:
                            v = r.byte()
                            if inter:
                                mx, my = v & 0xF, v >> 4
                                src = prev
                            else:
                                mx, my = v & 0xF, v >> 4
                                src = cur
                            for dy in (0, 1):
                                for dx in (0, 1):
                                    cur[idx(x1 + dx, y1 + dy)] = src[idx(x1 + dx + mx, y1 + dy + my)]
                    else:
                        for dy in (0, 1):
                            for dx in (0, 1):
                                cur[idx(x1 + dx, y1 + dy)] = r.byte()


class Decoder:
    def __init__(self, w, h, palsize):
        self.w, self.h, self.palsize = w, h, palsize
        self.cur, self.prev = bytearray(BUF), bytearray(BUF)
        self.pal = [(i, i, i) for i in range(256)]

    def frame(self, data):
        r = Reader(data)
        header = r.byte()
        if header & 0x40:
            for i in range(0, self.palsize):
                c = r.byte() << 16 | r.byte() << 8 | r.byte()
                self.pal[i] = (c >> 16, c >> 8 & 0xFF, c & 0xFF)
        bs = r.byte()
        if bs not in (8, 127):
            raise KmvcError("bad block size")
        method = header & 0x0F
        if method not in (0, 1, 3, 4):
            raise KmvcError("unknown method")
        cur = self.cur
        cur[:] = bytes(BUF)
        if method in (3, 4):
            try:
                decode_blocks(cur, self.prev, r, self.w, self.h, method == 4)
            except Stop:
                pass
        pic = bytes(cur[y * 320 + x] for y in range(self.h) for x in range(self.w))
        self.cur, self.prev = self.prev, self.cur
        return bool(header & 0x80), pic


def parse(data):
    if len(data) < 18 or data[:8] != MAGIC:
        raise KmvcError("not a KMVC frame sequence")
    w, h, palsize, count = (int.from_bytes(data[k:k + 2], "little") for k in range(8, 16, 2))
    if not w or not h or w > 320 or h > 200 or palsize >= 255:
        raise KmvcError("bad stream parameters")
    dec = Decoder(w, h, palsize)
    pos, frames = 16, []
    for _ in range(count):
        if pos + 4 > len(data):
            raise KmvcError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise KmvcError("frame data is truncated")
        pos += 4 + n
        try:
            key, pic = dec.frame(body)
            rgb = b"".join(bytes(dec.pal[i]) for i in pic)
            frames.append({"ok": True, "key": key, "crc32": "%08x" % zlib.crc32(rgb)})
        except KmvcError:
            frames.append({"ok": False, "key": None, "crc32": None})
    return {"width": w, "height": h, "frames": frames}
