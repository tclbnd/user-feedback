"""Sierra VMD video frames (.vmf): decode each frame record to RGB24 through the current palette."""
import zlib

MAGIC = b"VMDFRAME"
QUEUE = 0x1000


class VmdError(ValueError):
    pass


class Reader:
    def __init__(self, d, p=0):
        self.d, self.p = d, p

    def left(self):
        return max(0, len(self.d) - self.p)

    def byte(self):
        v = self.d[self.p] if self.p < len(self.d) else 0
        self.p += 1
        return v

    def peek(self):
        return self.d[self.p] if self.p < len(self.d) else 0

    def take(self, n):
        v = self.d[self.p:self.p + n]
        self.p += n
        return v


class Decoder:
    def __init__(self, w, h, lz_size):
        self.w, self.h, self.lz = w, h, lz_size
        self.prev = None
        self.palette = [(0, 0, 0)] * 256
        self.xo = self.yo = 0

    def frame(self, buf):
        if len(buf) < 16:
            raise VmdError("frame record is too short")
        w, h = self.w, self.h
        fx = int.from_bytes(buf[6:8], "little")
        fy = int.from_bytes(buf[8:10], "little")
        fw = int.from_bytes(buf[10:12], "little") - fx
        fh = int.from_bytes(buf[12:14], "little") - fy
        if fx < 0 or fw < 0 or fx >= w or fw > w or fx + fw > w:
            raise VmdError("bad horizontal range")
        if fy < 0 or fh < 0 or fy >= h or fh > h or fy + fh > h:
            raise VmdError("bad vertical range")
        pic = bytearray(w * h)
        r = Reader(buf, 16)
        palette = self.palette
        if buf[15] & 0x02:
            r.p += 2
            if r.left() < 768:
                raise VmdError("palette is truncated")
            palette = []
            for _ in range(256):
                palette.append(tuple((r.byte() * 4) & 0xFF for _ in range(3)))
            self.palette = palette
        if r.left() < 1:
            raise VmdError("no frame data")
        meth = r.byte()
        if meth & 0x80:
            raise VmdError("compressed frames are not supported")
        prev = self.prev
        for row in range(fh):
            base = (fy + row) * w + fx
            if meth == 2:
                if r.left() < fw:
                    raise VmdError("raw frame is truncated")
                pic[base:base + fw] = r.take(fw)
                continue
            if meth not in (1, 3):
                raise VmdError(f"unknown method {meth}")
            ofs = 0
            while True:
                n = r.byte()
                if n & 0x80:
                    n = (n & 0x7F) + 1
                    if True:
                        if ofs + n > fw or r.left() < n:
                            raise VmdError("literal run is too long")
                        pic[base + ofs:base + ofs + n] = r.take(n)
                        ofs += n
                else:
                    if ofs + n + 1 > fw or prev is None:
                        raise VmdError("copy run is too long")
                    pic[base + ofs:base + ofs + n + 1] = prev[base + ofs:base + ofs + n + 1]
                    ofs += n + 1
                if ofs >= fw:
                    break
            if ofs > fw:
                raise VmdError("row overflows")
        self.prev = bytes(pic)


def parse(data):
    if len(data) < 18 or data[:8] != MAGIC:
        raise VmdError("not a VMD frame file")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    lz = int.from_bytes(data[12:16], "little")
    count = int.from_bytes(data[16:18], "little")
    if not w or not h:
        raise VmdError("empty picture")
    dec = Decoder(w, h, lz)
    pos, frames = 18, []
    for _ in range(count):
        if pos + 4 > len(data):
            raise VmdError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        rec = data[pos + 4:pos + 4 + n]
        if len(rec) < n:
            raise VmdError("frame data is truncated")
        pos += 4 + n
        try:
            dec.frame(rec)
            rgb = b"".join(bytes(dec.palette[i]) for i in dec.prev)
            frames.append({"ok": True, "crc32": "%08x" % zlib.crc32(rgb)})
        except VmdError:
            frames.append({"ok": False, "crc32": None})
    return {"width": w, "height": h, "frames": frames,
            "colours": len(set(dec.prev)) if dec.prev is not None else 0}
