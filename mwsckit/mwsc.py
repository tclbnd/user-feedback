"""MatchWare screen capture (MWSC) frames: zlib-compressed runs of BGR pixels."""
import struct
import zlib


class MwscError(ValueError):
    pass


class Mwsc:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.stride = w * 3
        self.prev = None

    def frame(self, pkt):
        d = zlib.decompressobj()
        try:
            data = d.decompress(pkt)
        except zlib.error:
            raise MwscError("inflate failed")
        size = self.stride * self.h
        cur = bytearray(size)
        prev = self.prev if self.prev is not None else b""
        p = 0
        col = 0
        intra = True
        i = 0
        n = len(data)

        def put(v):
            nonlocal p
            if 0 <= p and p + 3 <= size:
                cur[p:p + 3] = v
            p += 3

        def wrap():
            nonlocal p, col
            if col == self.w:
                col = 0


        while i < n:
            fill = data[i:i + 3].ljust(3, b"\0")
            i += 3
            run = data[i] if i < n else 0
            i += 1
            if run == 255:
                raise MwscError("copy records are not supported")
            if run == 0:
                for _ in range(256):
                    wrap()
                    put(fill)
                    col += 1
            else:
                for _ in range(run):
                    wrap()
                    put(fill)
                    col += 1
        self.prev = bytes(cur)
        return cur, intra


def parse(data):
    if data[:8] != b"MWSCPACK":
        raise MwscError("not an MWSC pack")
    w, h, count = struct.unpack_from("<HHH", data, 8)
    if not w or not h:
        raise MwscError("empty picture")
    v = Mwsc(w, h)
    pos = 14
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise MwscError("frame table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        pkt = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(pkt) < n:
            raise MwscError("frame is truncated")
        try:
            cur, intra = v.frame(pkt)
        except MwscError:
            frames.append({"state": "error", "crc32": None})
            continue
        rows = [cur[r * v.stride:(r + 1) * v.stride] for r in range(h)]
        img = bytearray()
        for r in rows:
            for x in range(w):
                img += bytes(r[3 * x:3 * x + 3])
        frames.append({"state": "decoded", "key": intra, "crc32": "%08x" % zlib.crc32(bytes(img))})
    return {"width": w, "height": h, "frames": frames}
