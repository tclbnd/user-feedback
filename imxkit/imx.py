"""Simbiosis IMX video packets (320 x 160, 8-bit): skips, literal pixels and fills."""
import struct
import zlib

W, H = 320, 160
HIST = 32768


class ImxError(ValueError):
    pass


class Imx:
    def __init__(self):
        self.pix = bytearray(W * H)
        self.pal = [(0, 0, 0)] * 256
        self.hist = bytearray(HIST)
        self.pos = 0

    def frame(self, data):
        self.pix[:] = bytes(W * H)
        self.hist[:] = bytes(HIST)
        self.pos = 0
        d, p = data, 0
        get = lambda: d[p] if p < len(d) else 0
        x = y = 0
        while p < len(d) and x < W and y < H:
            b = d[p]
            p += 1
            n, op = b & 0x3F, b >> 6
            if op in (0, 3):
                if op == 3:
                    n = n * 256 + get()
                    p += 1
                while n > 0:
                    x += 1
                    n -= 1
                    if x >= W:
                        x = 0
                        y += 1
                    if y >= H:
                        break
            elif op == 1 and n == 0:
                raise ImxError("history copies are not supported")
            elif op == 1:
                while n > 0:
                    v = get()
                    p += 1
                    self.pix[x + y * W] = v
                    x += 1
                    n -= 1
                    if x >= W:
                        x = 0
                        y += 1
                    if y >= H:
                        break
            else:
                v = get()
                p += 1
                n += 1
                while n > 0:
                    self.pix[x + y * W] = v
                    x += 1
                    n -= 1
                    if x >= W:
                        x = 0
                        y += 1
                    if y >= H:
                        break


def parse(data):
    if data[:8] != b"IMXPACK1":
        raise ImxError("not an IMX pack")
    count, = struct.unpack_from("<H", data, 8)
    v = Imx()
    pos = 10
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise ImxError("packet table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        pkt = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(pkt) < n or n < 1:
            raise ImxError("packet is truncated")
        body = pkt[1:]
        v.pal = [(0, 0, 0)] * 256
        if pkt[0] & 1:
            if len(body) < 768:
                raise ImxError("palette is truncated")
            v.pal = [tuple(body[3 * k:3 * k + 3]) for k in range(256)]
            body = body[768:]
        try:
            v.frame(body)
        except ImxError:
            frames.append({"state": "error", "crc32": None})
            continue
        img = b"".join(bytes(v.pal[c]) for c in v.pix)
        frames.append({"state": "decoded", "crc32": "%08x" % zlib.crc32(img), "history": v.pos})
    return {"width": W, "height": H, "frames": frames}
