"""QuickDraw version 2 pictures (PICT): indexed, bitmap and direct pixel maps with PackBits rows."""
import zlib


class PictError(ValueError):
    pass


class Reader:
    def __init__(self, d, p=0):
        self.d, self.p = d, p

    def left(self):
        return len(self.d) - self.p

    def take(self, n):
        if n < 0 or self.left() < n:
            raise PictError("data is truncated")
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return int.from_bytes(self.take(2), "big")

    def u32(self):
        return int.from_bytes(self.take(4), "big")

    def skip(self, n):
        self.take(n)


def unpack_bits(src, unit, size):
    """PackBits: flag n < 128 copies n + 1 units, otherwise one unit is repeated."""
    out = bytearray()
    i = 0
    while i < len(src) and len(out) < size:
        n = src[i]
        i += 1
        if n < 128:
            k = (n + 1) * unit
            out += src[i:i + k]
            i += k
        else:
            out += src[i:i + unit] * (256 - n)
            i += unit
    out = out[:size]
    return bytes(out) + bytes(size - len(out))


def read_rows(g, rb, height, unit, unpacked_size, packed=True):
    rows = []
    for _ in range(height):
        if not packed:
            rows.append(g.take(unpacked_size))
            continue
        count = g.u8()
        rows.append(unpack_bits(g.take(count), unit, unpacked_size))
    return rows


def skip_region(g):
    g.skip(10)


def rect(g):
    top, left, bottom, right = g.u16(), g.u16(), g.u16(), g.u16()
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0 or w > 0x7FFF or h > 0x7FFF:
        raise PictError("bad bounds")
    return w, h


def pixmap_fields(g):
    g.skip(2)                      # pmVersion
    pack_type = g.u16()
    g.skip(4 + 4 + 4)              # packSize, hRes, vRes
    g.skip(2)                      # pixelType
    pixel_size = g.u16()
    cmp_count = g.u16()
    cmp_size = g.u16()
    g.skip(4 + 4 + 4)              # planeBytes, pmTable, pmReserved
    return pack_type, pixel_size, cmp_count, cmp_size


def colour_table(g):
    g.skip(4)                      # ctSeed
    flags = g.u16()
    n = g.u16() + 1
    if n > 256:
        raise PictError("colour table is too large")
    pal = [(0, 0, 0)] * 256
    for i in range(n):
        idx = g.u16()
        r, gg, b = g.u16() & 0xFF, g.u16() & 0xFF, g.u16() & 0xFF
        if idx < 256:
            pal[idx] = (r, gg, b)
    return pal


def bits_rect(g, region):
    rb_raw = g.u16()
    rb = rb_raw & 0x3FFF
    w, h = rect(g)
    if rb_raw & 0x8000:
        pack_type, pixel_size, cmp_count, cmp_size = pixmap_fields(g)
        if pixel_size not in (1, 2, 4, 8):
            raise PictError("unsupported indexed depth")
        pal = colour_table(g)
        kind = "indexed"
    else:
        pixel_size = 1
        pal = [(0, 0, 0), (255, 255, 255)]
        kind = "bitmap"
    if rb * 8 < w * pixel_size:
        raise PictError("row bytes too small")
    g.skip(8 + 8 + 2)              # srcRect, dstRect, mode
    if region:
        skip_region(g)
    rows = read_rows(g, rb, h, 1, rb)
    px = []
    per = 8 // pixel_size
    mask = (1 << pixel_size) - 1
    for row in rows:
        for x in range(w):
            byte = row[x // per]
            shift = pixel_size * (x % per)
            px.append(pal[(byte >> shift) & mask])
    return kind, pixel_size, w, h, px


def parse(data):
    if len(data) < 40:
        raise PictError("header is truncated")
    if data[10:12] == b"\x11\x01":
        raise PictError("version 1 pictures are not supported")
    if data[10:14] != b"\x00\x11\x02\xff":
        raise PictError("not a version 2 picture")
    g = Reader(data, 14)
    found = None
    while found is None:
        op = g.u16()
        if op in (0x0000, 0x001E):
            pass
        elif op == 0x0C00:
            g.skip(24)
        elif op == 0x0001:
            skip_region(g)
        elif op in (0x001A, 0x001B):
            g.skip(6)
        elif op == 0x00A0:
            g.skip(2)
        elif op == 0x00A1:
            g.skip(2)
            n = g.u16()
            g.skip(n)
        elif op in (0x0098, 0x0099):
            found = bits_rect(g, op == 0x0099)
        elif op in (0x009A, 0x009B):
            raise PictError("direct pixel maps are not supported")
        elif op == 0x00FF:
            raise PictError("picture has no pixel data")
        else:
            raise PictError("unsupported opcode %04x" % op)
    kind, depth, w, h, px = found
    img = b"".join(bytes(p) for p in px)
    return {"width": w, "height": h, "kind": kind, "depth": depth,
            "crc32": "%08x" % zlib.crc32(img), "colours": len(set(px))}
