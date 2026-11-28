"""Screenpresso screen recordings: zlib key and delta frames.

A packet's zlib stream holds a full picture-sized bitmap.  Key frames carry
the whole picture, delta frames a byte-wise difference that the Screenpresso
codec adds (modulo 256) to the previous picture.  Bitmaps are stored the
Windows DIB way: rows run bottom to top and every row is padded so the
distance between two rows (the stride) is a multiple of four bytes.
"""
import struct
import zlib


class SpresError(ValueError):
    pass


# Second packet byte is a power of two: 1 << bytes-per-pixel.
_PIXEL_SIZE_CODES = {4: 2, 8: 3, 16: 4}


class Spres:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.cs = None
        self.stride = 0
        self.buf = None
        self.keyed = False

    def packet(self, pkt):
        if len(pkt) < 2:
            raise SpresError("packet is too small")
        key = pkt[0] & 1
        cs = _PIXEL_SIZE_CODES.get(pkt[1])
        if cs is None:
            raise SpresError("unsupported pixel size")
        if not key and not self.keyed:
            raise SpresError("delta frame without a key frame")
        stride = (self.w * cs + 3) & ~3
        size = stride * self.h
        d = zlib.decompressobj()
        try:
            out = d.decompress(pkt[2:], size + 1)
        except zlib.error:
            raise SpresError("inflate failed")
        if not d.eof or len(out) != size:
            raise SpresError("inflate failed")
        self.cs, self.stride = cs, stride
        if self.buf is None or len(self.buf) != size:
            self.buf = bytearray(size)
        if key:
            self.buf[:] = out
            self.keyed = True
        else:
            # Delta: Screenpresso adds every byte to the previous picture.
            self.buf = bytearray(
                (a + b) & 0xFF for a, b in zip(self.buf, out)
            )

    def picture(self):
        cs, stride, w, h = self.cs, self.stride, self.w, self.h
        out = bytearray()
        for y in range(h):
            # Stored bottom-up: the last stored row is the first output row.
            row = self.buf[(h - 1 - y) * stride:]
            for x in range(w):
                o = x * cs
                if cs == 2:
                    v = row[o] | row[o + 1] << 8
                    comps = (v >> 10 & 31, v >> 5 & 31, v & 31)
                    out += bytes((c << 3) | (c >> 2) for c in comps)
                else:
                    # cs 3 (BGR) or 4 (BGR0); the report wants RGB, swap R/B.
                    out += bytes((row[o + 2], row[o + 1], row[o]))
        return bytes(out)


def parse(data):
    if data[:8] != b"SPRSPACK":
        raise SpresError("not a Screenpresso pack")
    w, h, count = struct.unpack_from("<HHH", data, 8)
    if not w or not h:
        raise SpresError("empty picture")
    v = Spres(w, h)
    pos = 14
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise SpresError("packet table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        pkt = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(pkt) < n:
            raise SpresError("packet is truncated")
        try:
            v.packet(pkt)
        except SpresError:
            frames.append({"state": "error", "crc32": None})
            continue
        frames.append({"state": "decoded", "key": bool(pkt[0] & 1), "bytes": v.cs, "crc32": "%08x" % zlib.crc32(v.picture())})
    return {"width": w, "height": h, "frames": frames}
