"""DXA video frame sequences (.dxs): decode each frame to 8-bit palette indices."""
import zlib

MAGIC = b"DXASEQ\0\0"


class DxaError(ValueError):
    pass


class Decoder:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.prev = None
        self.pal = [0] * 256
        self.decomp = bytes(w * h * 2)

    def frame(self, pkt):
        p = 0
        if pkt[0:4] == b"CMAP":
            p = 4
            for k in range(256):
                self.pal[k] = int.from_bytes(pkt[p:p + 3], "little")
                p += 3
        tag = pkt[p:p + 4]
        p += 4
        if tag == b"NULL":
            comp = -1
        else:
            if p >= len(pkt):
                raise DxaError("frame header is truncated")
            comp = pkt[p]
            p += 1
        if comp != -1:
            p += 4
            try:
                d = zlib.decompress(pkt[p:])
            except zlib.error:
                raise DxaError("compressed data is damaged")
            self.decomp = d[:self.w * self.h * 2]
        n = self.w * self.h
        src = self.decomp.ljust(n, b"\0")
        if comp == -1:
            pic, key = bytearray(n), False
        elif comp in (2, 4):
            pic, key = bytearray(src[:n]), True
        elif comp in (3, 5):
            if self.prev is None:
                raise DxaError("delta frame without a reference")
            pic, key = bytearray(src[:n]), False
        else:
            raise DxaError("unknown compression %d" % comp)
        self.prev = bytes(pic)
        return key


def parse(data):
    if len(data) < 16 or data[:8] != MAGIC:
        raise DxaError("not a DXA frame sequence")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    count = int.from_bytes(data[12:16], "little")
    if not w or not h or w % 4 or h % 4:
        raise DxaError("width and height must be non-zero multiples of 4")
    dec = Decoder(w, h)
    pos, frames = 16, []
    for _ in range(count):
        if pos + 4 > len(data):
            raise DxaError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise DxaError("frame data is truncated")
        pos += 4 + n
        try:
            key, ok = dec.frame(body), True
        except DxaError:
            key, ok = False, False
        pic = dec.prev if dec.prev is not None else bytes(w * h)
        frames.append({"ok": ok, "key": key, "crc32": "%08x" % zlib.crc32(pic)})
    pic = dec.prev if dec.prev is not None else bytes(w * h)
    rgb = b"".join(dec.pal[v].to_bytes(3, "big") for v in pic)
    return {"width": w, "height": h, "frames": frames, "colours": len(set(pic)), "rgb_crc32": "%08x" % zlib.crc32(rgb)}
