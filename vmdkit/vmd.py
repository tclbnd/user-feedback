"""Sierra VMD video frames (.vmf): decode each frame record to RGB24 through the current palette."""
import zlib

MAGIC = b"VMDFRAME"
QUEUE = 0x1000
COMPRESSED = 0x80


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


def lz_unpack(buf):
    """Sierra VMD LZSS: a flag byte per eight payload bytes, LSB first.

    Set bit  -> the next byte is copied literally.
    Clear bit -> a little-endian u16 back reference: high nibble gives the
                 run length minus 3, the low 12 bits give the absolute source
                 position inside the output buffer.
    The record starts with the unpacked size (u32) and the XOR key (u32).
    """
    if len(buf) < 8:
        raise VmdError("compressed frame is truncated")
    out_size = int.from_bytes(buf[:4], "little")
    r, out = 8, bytearray()
    while r < len(buf) and len(out) < out_size:
        flags = buf[r]
        r += 1
        for bit in range(8):
            if len(out) >= out_size:
                break
            if flags & (1 << bit):
                if r >= len(buf):
                    raise VmdError("literal byte is truncated")
                out.append(buf[r])
                r += 1
            else:
                if r + 2 > len(buf):
                    raise VmdError("back reference is truncated")
                word = int.from_bytes(buf[r:r + 2], "little")
                r += 2
                src = word & 0x0FFF
                run = (word >> 12) + 3
                if src >= len(out) or len(out) + run > out_size:
                    raise VmdError("back reference points outside the unpack buffer")
                for _ in range(run):
                    out.append(out[src])
                    src += 1
    if len(out) != out_size or r != len(buf):
        raise VmdError("compressed frame did not unpack to its declared size")
    return bytes(out)


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

        # The first frame anchors the movie's position on screen; every frame
        # stores absolute screen coordinates, so subtract the anchor to get
        # the coordinates inside the picture.
        if self.prev is None:
            self.xo = int.from_bytes(buf[6:8], "little")
            self.yo = int.from_bytes(buf[8:10], "little")
        fx = int.from_bytes(buf[6:8], "little") - self.xo
        fy = int.from_bytes(buf[8:10], "little") - self.yo
        # The right and bottom edges are inclusive.
        fw = int.from_bytes(buf[10:12], "little") - self.xo - fx + 1
        fh = int.from_bytes(buf[12:14], "little") - self.yo - fy + 1
        if fx < 0 or fy < 0 or fw <= 0 or fh <= 0 or fx + fw > w or fy + fh > h:
            raise VmdError("frame rectangle lies outside the picture")

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
        payload = r.take(r.left())
        if meth & COMPRESSED:
            payload = lz_unpack(payload)
            meth &= ~COMPRESSED
        if meth not in (1, 2, 3):
            raise VmdError(f"unknown method {meth}")

        # Keep the previous picture: a frame only paints its own rectangle.
        pic = bytearray(self.prev) if self.prev is not None else bytearray(w * h)
        prev = self.prev
        if meth == 2:
            if len(payload) != fw * fh:
                raise VmdError("raw frame has the wrong size")
            for row in range(fh):
                base = (fy + row) * w + fx
                pic[base:base + fw] = payload[row * fw:(row + 1) * fw]
            self.prev = bytes(pic)
            return

        # Each frame row is an independent RLE segment: runs never cross a
        # row boundary.  Split the payload into one segment per row; a segment
        # may paint fewer than fw pixels, in which case the remaining columns
        # keep the values already present in the picture.
        seg_len, extra = divmod(len(payload), fh)
        d = Reader(payload)
        for row in range(fh):
            seg_end = d.p + seg_len + (1 if row < extra else 0)
            base = (fy + row) * w + fx
            ofs = 0
            while d.p < seg_end:
                n = d.byte()
                if n == 0xFF and meth == 3:
                    # Solid run: a count byte (plus one) and one colour byte.
                    if seg_end - d.p < 2:
                        raise VmdError("solid run is truncated")
                    count = d.byte() + 1
                    colour = d.byte()
                    if ofs + count > fw:
                        raise VmdError("solid run is too long")
                    pic[base + ofs:base + ofs + count] = bytes([colour]) * count
                    ofs += count
                elif n & 0x80:
                    # Literal run.  At the end of a short row the run may be
                    # cut off by the segment boundary; clamp to those bytes.
                    count = (n & 0x7F) + 1
                    count = min(count, seg_end - d.p)
                    if count == 0:
                        raise VmdError("literal run is truncated")
                    if ofs + count > fw:
                        raise VmdError("literal run is too long")
                    pic[base + ofs:base + ofs + count] = d.take(count)
                    ofs += count
                else:
                    # Copy run: repaint columns from the previous picture.
                    count = n + 1
                    if prev is None or ofs + count > fw:
                        raise VmdError("copy run is too long")
                    pic[base + ofs:base + ofs + count] = \
                        prev[base + ofs:base + ofs + count]
                    ofs += count
        if d.left():
            raise VmdError("frame data does not split evenly into rows")
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
