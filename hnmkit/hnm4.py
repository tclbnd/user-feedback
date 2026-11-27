"""Cryo HNM4 still-frame chunks (.hnc): palette chunks and intra frames, decoded through the palette."""
import zlib

MAGIC = b"HNM4SEQ\0"
PL, IZ, SD = b"PL", b"IZ", b"SD"


class HnmError(ValueError):
    pass


class Reader:
    def __init__(self, d):
        self.d, self.p = d, 0

    def byte(self):
        v = self.d[self.p] if self.p < len(self.d) else 0
        self.p += 1
        return v

    def le(self, n):
        v = 0
        for k in range(n):
            v |= self.byte() << (8 * k)
        return v

    def tell(self):
        return min(self.p, len(self.d))


class _BitStream:
    """Control bits and literal/copy bytes are two independent streams.

    The control stream is a run of packed 32-bit little-endian words read
    LSB-first; the data stream holds the literal pixels and copy parameters as
    plain bytes.  The original implementation mixed both into one cursor, so a
    32-bit control-word refill jumped into the middle of the data bytes.
    """

    def __init__(self, d):
        self.d = d
        self.ctrl = 0
        self.word, self.bits_left = 0, 0
        self.data = 4  # first 32-bit control word occupies bytes 0..3
        self.exhausted = False

    def bit(self):
        if not self.bits_left:
            if self.ctrl + 4 > len(self.d):
                self.exhausted = True
                return 0
            self.word = int.from_bytes(
                self.d[self.ctrl:self.ctrl + 4], "little")
            self.ctrl += 4
            self.bits_left = 32
        b = self.word & 1
        self.word >>= 1
        self.bits_left -= 1
        return b

    def byte(self):
        if self.data >= len(self.d):
            self.exhausted = True
            return 0
        v = self.d[self.data]
        self.data += 1
        return v

    def word16(self):
        return self.byte() | (self.byte() << 8)


class Decoder:
    def __init__(self, version, w, h):
        self.version, self.w, self.h = version, w, h
        self.cur = bytearray(w * h)
        self.pal = [0xFF000000] * 256

    def palette(self, chunk):
        eight = False
        r = Reader(chunk[8:])
        size = len(chunk) - 8
        while r.tell() < size:
            start, count = r.byte(), r.byte()
            if start == 255 and count == 255:
                break
            off = start
            while count:
                v = r.byte() << 16 | r.byte() << 8 | r.byte()
                if not eight:
                    v = ((v >> 16 & 0xFF) * 4 & 0xFF) << 16 | ((v >> 8 & 0xFF) * 4 & 0xFF) << 8 | ((v & 0xFF) * 4 & 0xFF)
                self.pal[off % 256] = v | 0xFF000000
                off += 1
                count -= 1

    def intra(self, src):
        # The frame is two interlaced fields.  They are produced into one
        # linear buffer in order (even field then odd field); long copies can
        # reach back across the field boundary.  The caller re-interleaves the
        # two halves into display scan lines.
        total = self.w * self.h
        bs = _BitStream(src)
        cur = self.cur
        w = 0
        data_end = len(src)
        while w < total and not bs.exhausted and bs.data < data_end:
            if bs.bit():
                # literal pixel
                if w < total:
                    cur[w] = bs.byte()
                w += 1
                continue
            if bs.bit():
                # long back copy: 6-bit count, 10-bit distance, little-endian
                word = bs.word16()
                count = (word >> 10) & 0x3F
                dist = word & 0x3FF
                if not count:
                    count = bs.byte()
                    if not count:
                        break
                # the stored count is two short of the pixels actually copied
                count += 2
                src_pos = w - dist
                if not (0 <= src_pos < total):
                    # reference before the frame start: nothing is copied
                    continue
                for _ in range(count):
                    if w >= total or src_pos >= total:
                        break
                    cur[w] = cur[src_pos]
                    w += 1
                    src_pos += 1
            else:
                # short run: two count bits (LSB first) + one signed delta byte
                count = bs.bit() + 2 * bs.bit() + 1
                raw = bs.byte()
                delta = raw - 0x100 if raw >= 0x80 else raw
                for _ in range(count):
                    if w >= total:
                        break
                    if w:
                        cur[w] = (cur[w - 1] + delta) & 0xFF
                    w += 1

    def picture(self):
        # Re-interleave the two fields: linear half 0 holds the even scan
        # lines, half 1 the odd ones.
        w, h = self.w, self.h
        half = h // 2
        fieldpx = half * w
        flat = self.cur
        out = bytearray(w * h)
        for y in range(half):
            out[(2 * y) * w:(2 * y + 1) * w] = flat[y * w:(y + 1) * w]
            out[(2 * y + 1) * w:(2 * y + 2) * w] = \
                flat[fieldpx + y * w:fieldpx + (y + 1) * w]
        return bytes(out)




def parse(data):
    if len(data) < 16 or data[:8] != MAGIC:
        raise HnmError("not an HNM4 chunk sequence")
    version = data[8]
    w = int.from_bytes(data[10:12], "little")
    h = int.from_bytes(data[12:14], "little")
    count = int.from_bytes(data[14:16], "little")
    if not w or not h or h % 2:
        raise HnmError("width must be non-zero and height even")
    dec = Decoder(version, w, h)
    pos, frames, palettes, skipped = 16, [], 0, 0
    for _ in range(count):
        if pos + 4 > len(data):
            raise HnmError("chunk table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        ch = data[pos + 4:pos + 4 + n]
        if len(ch) < n:
            raise HnmError("chunk is truncated")
        pos += 4 + n
        if len(ch) < 8:
            skipped += 1
            continue
        cid = ch[4:6]
        if cid == PL:
            dec.palette(ch)
            palettes += 1
        elif cid == IZ:
            if len(ch) < 12:
                skipped += 1
                continue
            # Do not clear the picture buffer: the player keeps the previous
            # frame wherever an intra frame does not write a pixel.
            dec.intra(ch[12:])
            pic = dec.picture()
            rgb = b"".join(bytes([v >> 16 & 0xFF, v >> 8 & 0xFF, v & 0xFF]) for v in (dec.pal[i] for i in pic))
            frames.append({"crc32": "%08x" % zlib.crc32(rgb), "colours": len(set(pic))})
        else:
            skipped += 1
    return {"version": version, "width": w, "height": h, "palettes": palettes, "frames": frames, "skipped": skipped}
