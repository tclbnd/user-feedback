"""Netpbm images: PBM, PGM, PPM (plain and raw) and PAM, several images per stream."""
import zlib

WS = frozenset(b" \t\n\v\f\r")

# PAM tuple types that carry an alpha channel (Netpbm libnetpbm tupltype names).
_ALPHA_TYPES = frozenset((
    b"BLACKANDWHITE_ALPHA", b"GRAYSCALE_ALPHA", b"RGB_ALPHA",
))
_BLACKWHITE_TYPES = frozenset((b"BLACKANDWHITE", b"BLACKANDWHITE_ALPHA"))


class PnmError(ValueError):
    pass


class Stream:
    def __init__(self, d):
        self.d, self.p = d, 0

    def skip_space(self):
        """Skip Netpbm whitespace; a '#' runs a comment to the next newline."""
        d = self.d
        n = len(d)
        while self.p < n:
            c = d[self.p]
            if c in WS:
                self.p += 1
            elif c == 0x23:  # '#'
                self.p += 1
                while self.p < n and d[self.p] != 0x0a:
                    self.p += 1
            else:
                break

    def number(self):
        self.skip_space()
        s = self.p
        d = self.d
        n = len(d)
        while self.p < n and 0x30 <= d[self.p] <= 0x39:
            self.p += 1
        if s == self.p:
            raise PnmError("number expected")
        return int(d[s:self.p])

    def token(self):
        """Read a whitespace/comment-delimited token (used for PAM header keywords)."""
        self.skip_space()
        s = self.p
        d = self.d
        n = len(d)
        while self.p < n and d[self.p] not in WS and d[self.p] != 0x23:
            self.p += 1
        if s == self.p:
            raise PnmError("token expected")
        return d[s:self.p]

    def one_space(self):
        """Exactly one whitespace character separates a raw raster from its header."""
        if self.p >= len(self.d) or self.d[self.p] not in WS:
            raise PnmError("whitespace expected after the header")
        self.p += 1

    def take(self, n):
        if len(self.d) - self.p < n:
            raise PnmError("raster is truncated")
        b = self.d[self.p:self.p + n]
        self.p += n
        return b


def scale(v, maxval):
    """Map a sample onto 0-255: round(v * 255 / maxval), halves rounded up."""
    return (2 * v * 255 + maxval) // (2 * maxval)


def _summarise(kind, w, h, maxval, alpha, px):
    img = b"".join(bytes(p) for p in px)
    return {"format": "P%d" % kind, "width": w, "height": h, "maxval": maxval,
            "alpha": alpha, "crc32": "%08x" % zlib.crc32(img),
            "colours": len(set(px))}


def _read_classic(s, kind):
    """P1-P6: PBM, PGM, PPM in plain or raw (binary) encoding."""
    w = s.number()
    h = s.number()
    is_pbm = kind in (1, 4)
    maxval = 1 if is_pbm else s.number()
    if w < 1 or h < 1:
        raise PnmError("empty image")
    if not is_pbm and not 1 <= maxval <= 65535:
        raise PnmError("bad maxval")
    if kind != 1:
        # Single blank separates the header from a raw raster.
        s.one_space()

    n = w * h
    px = []
    if kind == 1:  # plain PBM: 1 is black, 0 is white
        for _ in range(n):
            bit = s.number()
            if bit not in (0, 1):
                raise PnmError("bad bit")
            v = 0 if bit else 255
            px.append((v, v, v))
        return _summarise(kind, w, h, 1, False, px)

    if kind == 4:  # raw PBM: MSB first, every row padded to a whole byte
        row_bytes = (w + 7) // 8
        for _ in range(h):
            row = s.take(row_bytes)
            for x in range(w):
                bit = (row[x >> 3] >> (7 - (x & 7))) & 1
                v = 0 if bit else 255
                px.append((v, v, v))
        return _summarise(kind, w, h, 1, False, px)

    depth = 3 if kind in (3, 6) else 1
    total = n * depth
    if kind in (2, 3):  # plain PGM/PPM
        vals = [s.number() for _ in range(total)]
    else:                # raw PGM/PPM, big-endian samples
        size = 2 if maxval > 255 else 1
        raw = s.take(total * size)
        if size == 1:
            vals = list(raw)
        else:
            vals = [(raw[2 * k] << 8) | raw[2 * k + 1] for k in range(total)]
    vals = [scale(v, maxval) for v in vals]
    for k in range(n):
        t = vals[k * depth:(k + 1) * depth]
        if depth == 1:
            px.append((t[0], t[0], t[0]))
        else:
            px.append((t[0], t[1], t[2]))
    return _summarise(kind, w, h, maxval, False, px)


def _infer_tupltype(depth, maxval):
    """Netpbm assigns a default tuple type when TUPLTYPE is absent."""
    if depth == 1:
        return b"BLACKANDWHITE" if maxval == 1 else b"GRAYSCALE"
    if depth == 2:
        return b"BLACKANDWHITE_ALPHA" if maxval == 1 else b"GRAYSCALE_ALPHA"
    if depth == 3:
        return b"RGB"
    if depth == 4:
        return b"RGB_ALPHA"
    return b""


def _read_pam(s):
    """P7 PAM: line-oriented TEXT header ending in ENDHDR, then a raw raster."""
    width = height = depth = maxval = None
    tupltype = None
    while True:
        key = s.token()
        if key == b"ENDHDR":
            break
        if key == b"WIDTH":
            width = s.number()
        elif key == b"HEIGHT":
            height = s.number()
        elif key == b"DEPTH":
            depth = s.number()
        elif key == b"MAXVAL":
            maxval = s.number()
        elif key == b"TUPLTYPE":
            tupltype = s.token()
        else:
            raise PnmError("unknown PAM header token %r" % key)
    if width is None or height is None or depth is None or maxval is None:
        raise PnmError("PAM header missing a required field")
    if width < 1 or height < 1 or depth < 1:
        raise PnmError("empty image")
    if not 1 <= maxval <= 65535:
        raise PnmError("bad maxval")
    if tupltype is None:
        tupltype = _infer_tupltype(depth, maxval)
    alpha = tupltype in _ALPHA_TYPES
    blackwhite = tupltype in _BLACKWHITE_TYPES

    # A single whitespace character (normally newline) ends the ENDHDR line.
    s.one_space()

    size = 2 if maxval > 255 else 1
    raw = s.take(width * height * depth * size)
    if size == 1:
        vals = list(raw)
    else:  # PAM samples are unsigned big-endian integers
        vals = [(raw[2 * k] << 8) | raw[2 * k + 1] for k in range(width * height * depth)]

    px = []
    for k in range(width * height):
        t = vals[k * depth:(k + 1) * depth]
        if blackwhite:
            # B&W tuple type keeps PBM bit semantics: 1 = black, 0 = white.
            v = 0 if t[0] else 255
            px.append((v, v, v))
        elif depth in (1, 2):
            g = scale(t[0], maxval)
            px.append((g, g, g))
        else:
            px.append((scale(t[0], maxval), scale(t[1], maxval), scale(t[2], maxval)))
    return _summarise(7, width, height, maxval, alpha, px)


def one(s):
    magic = s.take(2)
    if magic[:1] != b"P" or magic[1:] not in b"1234567":
        raise PnmError("not a Netpbm image")
    kind = magic[1] - 0x30
    if kind == 7:
        return _read_pam(s)
    return _read_classic(s, kind)


def parse(data):
    s = Stream(data)
    images = []
    while True:
        s.skip_space()
        if s.p >= len(s.d):
            break
        images.append(one(s))
    if not images:
        raise PnmError("not a Netpbm image")
    return {"images": images}
