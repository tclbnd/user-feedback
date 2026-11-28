"""Electronic Arts CMV video (.cmv): MVIh headers with palettes, intra frames and 4 x 4 motion frames over two references."""
import zlib


class CmvError(ValueError):
    pass


def le16(b, p):
    return b[p] | b[p + 1] << 8


class Decoder:
    def __init__(self):
        self.w = self.h = 0
        self.pal = [(0, 0, 0)] * 256
        self.last = self.last2 = None

    def header(self, buf, p, end):
        if end - p < 16:
            raise CmvError("header is truncated")
        w, h = le16(buf, p + 4), le16(buf, p + 6)
        self.w, self.h = w, h
        start, count = le16(buf, p + 12), le16(buf, p + 14)
        q = p + 16
        i = 0
        start = 0
        while i < start + count and i < 256 and end - q >= 3:
            self.pal[i] = tuple(buf[q:q + 3])
            q += 3
            i += 1

    def motion(self, dst, ref, x, y, dx, dy):
        w, h = self.w, self.h
        for j in range(y, y + 4):
            for i in range(x, x + 4):
                sx = min(max(i + dx, 0), w - 1)
                sy = min(max(j + dy, 0), h - 1)
                dst[j * w + i] = ref[sy * w + sx]

    def frame(self, buf):
        end = len(buf)
        if end < 8:
            raise CmvError("packet is too short")
        p = 0
        tag = buf[0:4]
        if tag == b"MVIh":
            size = int.from_bytes(buf[4:8], "little")
            self.header(buf, 8, end)
            if size > end - 8:
                raise CmvError("header size runs past the packet")
            p += size
        if not self.w or not self.h:
            raise CmvError("no picture size yet")
        w, h = self.w, self.h
        pic = bytearray(w * h)
        p += 8
        key = not (buf[p] & 1) if p < end else True
        d = p + 2
        if key:
            for y in range(h):
                if end - d < w:
                    raise CmvError("intra frame is truncated")
                pic[y * w:(y + 1) * w] = buf[d:d + w]
                d += w
        else:
            raw = d + (w * h // 16)
            i = 0
            for y in range(h // 4):
                for x in range(w // 4):
                    if end - d <= i:
                        break
                    v = buf[d + i]
                    if v == 0xFF:
                        if raw + 16 <= end and buf[raw] == 0xFF:
                            raw += 1
                            for r in range(4):
                                pic[(y * 4 + r) * w + x * 4:(y * 4 + r) * w + x * 4 + 4] = buf[raw + 4 * r:raw + 4 * r + 4]
                            raw += 16
                        elif raw < end:
                            mv = buf[raw]
                            if self.last is not None:
                                self.motion(pic, self.last, x * 4, y * 4, (mv & 0xF) - 8, (mv >> 4) - 8)
                            raw += 1
                    else:
                        if self.last is not None:
                            self.motion(pic, self.last, x * 4, y * 4, (v & 0xF) - 8, (v >> 4) - 8)
                    i += 1
        self.last2, self.last = self.last, pic
        return key, pic


def parse(data):
    if len(data) < 10 or data[:8] != b"CMVMOVIE":
        raise CmvError("not a CMV movie")
    count = int.from_bytes(data[8:10], "little")
    pos = 10
    dec = Decoder()
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise CmvError("packet table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        pkt = data[pos + 4:pos + 4 + n]
        if len(pkt) < n:
            raise CmvError("packet is truncated")
        pos += 4 + n
        try:
            key, pic = dec.frame(pkt)
            rgb = b"".join(bytes(dec.pal[v]) for v in pic)
            frames.append({"ok": True, "key": key, "crc32": "%08x" % zlib.crc32(rgb)})
        except CmvError:
            frames.append({"ok": False, "key": None, "crc32": None})
    return {"width": dec.w, "height": dec.h, "frames": frames}
