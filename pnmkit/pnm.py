"""Netpbm images: PBM, PGM, PPM (plain and raw) and PAM, several images per stream."""
import zlib

WS = b" \t\n\v\f\r"


class PnmError(ValueError):
    pass


class Stream:
    def __init__(self, d):
        self.d, self.p = d, 0

    def skip_space(self):
        while self.p < len(self.d):
            c = self.d[self.p]
            if c in WS:
                self.p += 1
            else:
                break

    def number(self):
        self.skip_space()
        s = self.p
        while self.p < len(self.d) and 0x30 <= self.d[self.p] <= 0x39:
            self.p += 1
        if s == self.p:
            raise PnmError("number expected")
        return int(self.d[s:self.p])

    def one_space(self):
        if self.p >= len(self.d) or self.d[self.p] not in WS:
            raise PnmError("whitespace expected after the header")
        while self.p < len(self.d) and self.d[self.p] in WS:
            self.p += 1

    def take(self, n):
        if len(self.d) - self.p < n:
            raise PnmError("raster is truncated")
        b = self.d[self.p:self.p + n]
        self.p += n
        return b


def scale(v, maxval):
    return min(v, 255)


def one(s):
    magic = s.take(2)
    if magic[:1] != b"P" or magic[1:] not in b"1234567":
        raise PnmError("not a Netpbm image")
    kind = magic[1] - 0x30
    alpha = False
    if kind == 7:
        raise PnmError("PAM images are not supported")
    w, h = s.number(), s.number()
    maxval = 1 if kind in (1, 4) else s.number()
    depth = 3 if kind in (3, 6) else 1
    if kind != 1:
        s.one_space()
    if w < 1 or h < 1:
        raise PnmError("empty image")
    if maxval < 1:
        raise PnmError("bad maxval")
    px = []
    if kind == 1:
        for _ in range(w * h):
            c = b"%d" % s.number()
            if c not in (b"0", b"1"):
                raise PnmError("bad bit")
            v = 255 if c == b"1" else 0
            px.append((v, v, v))
    elif kind == 4:
        bits = s.take((w * h + 7) // 8)
        for k in range(w * h):
            v = 255 if bits[k >> 3] >> (7 - (k & 7)) & 1 else 0
            px.append((v, v, v))
    else:
        n = w * h * depth
        if kind in (2, 3):
            vals = [s.number() for _ in range(n)]
        else:
            size = 2 if maxval > 255 else 1
            raw = s.take(n * size)
            vals = list(raw) if size == 1 else [raw[2 * k] | raw[2 * k + 1] << 8 for k in range(n)]
        vals = [scale(v, maxval) for v in vals]
        for k in range(w * h):
            t = vals[k * depth:(k + 1) * depth]
            if depth == 1:
                px.append((t[0], t[0], t[0]))
            else:
                px.append(tuple(t[:3]))
    img = b"".join(bytes(p) for p in px)
    return {"format": "P%d" % kind, "width": w, "height": h, "maxval": maxval, "alpha": alpha,
            "crc32": "%08x" % zlib.crc32(img), "colours": len(set(px))}


def parse(data):
    s = Stream(data)
    return {"images": [one(s)]}
