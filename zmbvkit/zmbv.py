"""Zip Motion Blocks Video (ZMBV) decoder, as written by DOSBox captures."""
import zlib

MAGIC = b"ZMBVPAK\0"
FORMATS = {4: 1, 6: 2, 8: 4}   # 8 bpp palettised, 16 bpp RGB565, 32 bpp


class ZmbvError(Exception):
    pass


class Decoder:
    def __init__(self, width, height):
        self.w, self.h = width, height
        self.fmt = None
        self.cur = self.prev = None
        self.pal = bytearray(768)
        self.z = None

    def frame(self, buf):
        if not buf:
            raise ZmbvError("empty frame")
        flags, buf = buf[0], buf[1:]
        if flags & 1:
            if len(buf) < 6:
                raise ZmbvError("key frame header is truncated")
            hi, lo, comp, fmt, bw, bh = buf[:6]
            buf = buf[6:]
            if comp not in (0, 1):
                raise ZmbvError("unsupported compression %d" % comp)
            if fmt not in FORMATS:
                raise ZmbvError("unsupported format %d" % fmt)
            if bw == 0 or bh == 0:
                raise ZmbvError("zero block size")
            self.comp, self.fmt, self.bw, self.bh = comp, fmt, bw, bh
            self.bpp = FORMATS[fmt]
            size = self.w * self.h * self.bpp
            if self.cur is None or len(self.cur) != size:
                self.cur = bytearray(size)
                self.prev = bytearray(size)
            # One zlib stream is continued by all chunks between key frames.
            self.z = zlib.decompressobj() if comp == 1 else None
        if self.fmt is None:
            raise ZmbvError("inter frame before key frame")
        if self.comp == 1:
            try:
                data = self.z.decompress(buf)
            except zlib.error as exc:
                raise ZmbvError("zlib: %s" % exc)
        else:
            data = buf
        if flags & 1:
            self.intra(data)
            self.prev[:] = self.cur
        else:
            self.xor(data, flags)
        return bool(flags & 1)

    def intra(self, src):
        p = 0
        if self.fmt == 4:
            if len(src) < 768:
                raise ZmbvError("palette is truncated")
            self.pal[:] = src[:768]
            p = 768
        size = len(self.cur)
        if len(src) - p < size:
            raise ZmbvError("key frame picture is truncated")
        self.cur[:] = src[p:p + size]

    def xor(self, src, flags):
        w, h, bw, bh, bpp = self.w, self.h, self.bw, self.bh, self.bpp
        bx, by = (w + bw - 1) // bw, (h + bh - 1) // bh
        p = 0
        if self.fmt == 4 and flags & 2:
            if len(src) < 768:
                raise ZmbvError("palette change is truncated")
            self.pal[:] = src[:768]
            p = 768
        if len(src) - p < 2 * bx * by:
            raise ZmbvError("motion vectors are truncated")
        mv = src[p:p + 2 * bx * by]
        p += bx * by * 2

        nxt = bytearray(len(self.cur))
        block = 0
        for y in range(0, h, bh):
            bh2 = min(bh, h - y)
            for x in range(0, w, bw):
                bw2 = min(bw, w - x)
                vx, vy = mv[block], mv[block + 1]
                block += 2

                # Bit 0 of the x-vector selects the optional block update.
                # The actual vector is the signed byte formed by the other bits.
                dx = (vx & 0xfe) - 256 if vx & 0x80 else (vx & 0xfe)
                dy = vy - 256 if vy & 0x80 else vy
                mx, my = x + dx, y + dy
                for j in range(bh2):
                    o = ((y + j) * w + x) * bpp
                    sx0 = min(max(mx, 0), w - bw2)
                    sy = min(max(my + j, 0), h - 1)
                    s = (sy * w + sx0) * bpp
                    nxt[o:o + bw2 * bpp] = self.prev[s:s + bw2 * bpp]

                if vx & 1:
                    # The encoder emits complete blocks, including padding
                    # outside the picture at the right and bottom edges.
                    row_width = bw * bpp
                    if p + bh * row_width > len(src):
                        raise ZmbvError("block update is truncated")
                    for j in range(bh2):
                        o = ((y + j) * w + x) * bpp
                        for k in range(bw2 * bpp):
                            nxt[o + k] = src[p + k]
                        p += row_width
                    p += (bh - bh2) * row_width

        self.prev, self.cur = self.cur, nxt

    def picture(self):
        if self.fmt == 4:
            return b"".join(self.pal[3 * v:3 * v + 3] for v in self.cur)
        return bytes(self.cur)


def parse(data):
    if len(data) < 14 or data[:8] != MAGIC:
        raise ZmbvError("not a ZMBV frame pack")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    count = int.from_bytes(data[12:14], "little")
    if w == 0 or h == 0:
        raise ZmbvError("empty picture")
    dec = Decoder(w, h)
    pos, frames = 14, []
    for _ in range(count):
        if pos + 4 > len(data):
            raise ZmbvError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise ZmbvError("frame data is truncated")
        pos += 4 + n
        key = dec.frame(body)
        frames.append({"key": key, "crc32": "%08x" % zlib.crc32(dec.picture())})
    bits = {4: 8, 6: 16, 8: 32}.get(dec.fmt)
    return {"width": w, "height": h, "bits": bits, "frames": frames}
