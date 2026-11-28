"""Screenpresso screen recordings: zlib key frames."""
import struct
import zlib


class SpresError(ValueError):
    pass


class Spres:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.cs = None
        self.rows = None
        self.buf = bytearray(w * h * 4)

    def packet(self, pkt):
        if len(pkt) < 2:
            raise SpresError("packet is too small")
        key = pkt[0] & 1
        cs = (pkt[1] & 3) + 1
        if cs not in (1, 2, 3, 4):
            raise SpresError("unsupported pixel size")
        d = zlib.decompressobj()
        try:
            out = d.decompress(pkt[2:], len(self.buf) + 1)
        except zlib.error:
            raise SpresError("inflate failed")
        if not d.eof:
            raise SpresError("inflate failed")
        self.buf[:] = out[:len(self.buf)].ljust(len(self.buf), b"\0")
        if self.rows is None or cs != self.cs:
            self.cs = cs
            self.rows = [bytearray(self.w * cs) for _ in range(self.h)]
        bw = self.w * cs
        src = bw
        if not key:
            raise SpresError("delta frames are not supported")
        for y in range(self.h):
            self.rows[y][:] = self.buf[y * src:y * src + bw]

    def picture(self):
        out = bytearray()
        for r in self.rows:
            for x in range(self.w):
                if self.cs == 1:
                    out += bytes([r[x]] * 3)
                elif self.cs == 2:
                    v = r[2 * x] | r[2 * x + 1] << 8
                    out += bytes((c << 3) for c in (v >> 10 & 31, v >> 5 & 31, v & 31))
                else:
                    o = x * self.cs
                    out += bytes(r[o:o + 3])
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
