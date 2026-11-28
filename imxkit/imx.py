"""Simbiosis IMX video packets (320 x 160, 8-bit): skips, literal pixels,
history copies and fills.

The picture, the 32 KB history ring and the palette all live from packet to
packet, exactly like the game's player: a packet only touches the pixels it
names, every pixel it draws (literal, fill or history copy) is also appended
to the history, and skips leave both untouched.
"""
import struct
import zlib

W, H = 320, 160
HIST = 32768
NPIX = W * H


class ImxError(ValueError):
    pass


class Imx:
    def __init__(self):
        self.pix = bytearray(NPIX)
        self.pal = [(0, 0, 0)] * 256
        self.hist = bytearray(HIST)
        self.pos = 0

    def frame(self, data):
        d, p = data, 0
        get = lambda: d[p] if p < len(d) else 0
        pos = 0
        while p < len(d) and pos < NPIX:
            b = d[p]
            p += 1
            n, op = b & 0x3F, b >> 6
            if op in (0, 3):
                # Skip: move the draw cursor without drawing anything.
                if op == 3:
                    n = n * 256 + get()
                    p += 1
                pos += n
            elif op == 1 and n == 0:
                # History copy: u16 little-endian absolute history offset,
                # then the number of bytes to copy.
                off = get()
                p += 1
                off |= get() << 8
                p += 1
                n = get()
                p += 1
                while n > 0 and pos < NPIX:
                    v = self.hist[off % HIST]
                    self.pix[pos] = v
                    self.hist[self.pos] = v
                    self.pos = (self.pos + 1) % HIST
                    off += 1
                    pos += 1
                    n -= 1
            elif op == 1:
                # Literal pixels, each one also going into the history.
                while n > 0 and pos < NPIX:
                    v = get()
                    p += 1
                    self.pix[pos] = v
                    self.hist[self.pos] = v
                    self.pos = (self.pos + 1) % HIST
                    pos += 1
                    n -= 1
            else:
                # Fill of exactly n pixels (the low six bits are the count).
                v = get()
                p += 1
                while n > 0 and pos < NPIX:
                    self.pix[pos] = v
                    self.hist[self.pos] = v
                    self.pos = (self.pos + 1) % HIST
                    pos += 1
                    n -= 1


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
