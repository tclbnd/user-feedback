"""BRender pixel maps (.pix): header chunk, optional palette chunk and the image data chunk."""
import struct
import zlib

FORMATS = {3: ("pal8", 1), 4: ("rgb565", 2), 5: ("rgb555", 2), 6: ("rgb24", 3), 7: ("xrgb", 4), 8: ("argb", 4)}

# BRender standard palette (std.pal): 64 greys, then 32-step ramps of blue, green, cyan, red, magenta and yellow.
STD_PAL = [
    0x000000, 0x030303, 0x060606, 0x090909, 0x0C0C0C, 0x0F0F0F, 0x121212, 0x151515,
    0x181818, 0x1B1B1B, 0x1E1E1E, 0x212121, 0x242424, 0x272727, 0x2A2A2A, 0x2D2D2D,
    0x313131, 0x343434, 0x373737, 0x3A3A3A, 0x3D3D3D, 0x404040, 0x434343, 0x464646,
    0x494949, 0x4C4C4C, 0x4F4F4F, 0x525252, 0x555555, 0x585858, 0x5B5B5B, 0x5E5E5E,
    0x626262, 0x656565, 0x686868, 0x6B6B6B, 0x6E6E6E, 0x717171, 0x747474, 0x777777,
    0x7A7A7A, 0x7D7D7D, 0x808080, 0x838383, 0x868686, 0x898989, 0x8C8C8C, 0x8F8F8F,
    0x939393, 0x999999, 0xA0A0A0, 0xA7A7A7, 0xAEAEAE, 0xB4B4B4, 0xBBBBBB, 0xC2C2C2,
    0xC9C9C9, 0xCFCFCF, 0xD6D6D6, 0xDDDDDD, 0xE4E4E4, 0xEAEAEA, 0xF1F1F1, 0xF8F8F8,
    0x000000, 0x020209, 0x050513, 0x07071D, 0x0A0A27, 0x0C0C31, 0x0F0F3B, 0x111145,
    0x14144F, 0x161659, 0x181863, 0x1B1B6D, 0x1E1E77, 0x202080, 0x22228A, 0x252594,
    0x28289E, 0x2A2AA8, 0x2D2DB2, 0x2F2FBC, 0x3131C6, 0x3434D0, 0x3737DA, 0x3939E4,
    0x3C3CEE, 0x5454F0, 0x6C6CF2, 0x8585F4, 0x9D9DF6, 0xB5B5F8, 0xCECEFA, 0xE6E6FC,
    0x000000, 0x020902, 0x051305, 0x071D07, 0x0A270A, 0x0C310C, 0x0F3B0F, 0x114511,
    0x144F14, 0x165916, 0x186318, 0x1B6D1B, 0x1E771E, 0x208020, 0x228A22, 0x259425,
    0x289E28, 0x2AA82A, 0x2DB22D, 0x2FBC2F, 0x31C631, 0x34D034, 0x37DA37, 0x39E439,
    0x3CEE3C, 0x54F054, 0x6CF26C, 0x85F485, 0x9DF69D, 0xB5F8B5, 0xCEFACE, 0xE6FCE6,
    0x000000, 0x020909, 0x051313, 0x071D1D, 0x0A2727, 0x0C3131, 0x0F3B3B, 0x114545,
    0x144F4F, 0x165959, 0x186363, 0x1B6D6D, 0x1E7777, 0x208080, 0x228A8A, 0x259494,
    0x289E9E, 0x2AA8A8, 0x2DB2B2, 0x2FBCBC, 0x31C6C6, 0x34D0D0, 0x37DADA, 0x39E4E4,
    0x3CEEEE, 0x54F0F0, 0x6CF2F2, 0x85F4F4, 0x9DF6F6, 0xB5F8F8, 0xCEFAFA, 0xE6FCFC,
    0x000000, 0x090202, 0x130505, 0x1D0707, 0x270A0A, 0x310C0C, 0x3B0F0F, 0x451111,
    0x4F1414, 0x591616, 0x631818, 0x6D1B1B, 0x771E1E, 0x802020, 0x8A2222, 0x942525,
    0x9E2828, 0xA82A2A, 0xB22D2D, 0xBC2F2F, 0xC63131, 0xD03434, 0xDA3737, 0xE43939,
    0xEE3C3C, 0xF05454, 0xF26C6C, 0xF48585, 0xF69D9D, 0xF8B5B5, 0xFACECE, 0xFCE6E6,
    0x000000, 0x090209, 0x130513, 0x1D071D, 0x270A27, 0x310C31, 0x3B0F3B, 0x451145,
    0x4F144F, 0x591659, 0x631863, 0x6D1B6D, 0x771E77, 0x802080, 0x8A228A, 0x942594,
    0x9E289E, 0xA82AA8, 0xB22DB2, 0xBC2FBC, 0xC631C6, 0xD034D0, 0xDA37DA, 0xE439E4,
    0xEE3CEE, 0xF054F0, 0xF26CF2, 0xF485F4, 0xF69DF6, 0xF8B5F8, 0xFACEFA, 0xFCE6FC,
    0x000000, 0x090902, 0x131305, 0x1D1D07, 0x27270A, 0x31310C, 0x3B3B0F, 0x454511,
    0x4F4F14, 0x595916, 0x636318, 0x6D6D1B, 0x77771E, 0x808020, 0x8A8A22, 0x949425,
    0x9E9E28, 0xA8A82A, 0xB2B22D, 0xBCBC2F, 0xC6C631, 0xD0D034, 0xDADA37, 0xE4E439,
    0xEEEE3C, 0xF0F054, 0xF2F26C, 0xF4F485, 0xF6F69D, 0xF8F8B5, 0xFAFACE, 0xFCFCE6,
]

class PixError(ValueError):
    pass


class In:
    def __init__(self, d):
        self.d, self.p = d, 0

    def left(self):
        return max(0, len(self.d) - self.p)

    def be32(self):
        b = self.d[self.p:self.p + 4].ljust(4, b"\0")
        self.p += 4
        return int.from_bytes(b, "big")

    def be16(self):
        b = self.d[self.p:self.p + 2].ljust(2, b"\0")
        self.p += 2
        return int.from_bytes(b, "big")

    def byte(self):
        v = self.d[self.p] if self.p < len(self.d) else 0
        self.p += 1
        return v


def header(g):
    n = g.be32()
    fmt = g.byte()
    g.p += 2
    w, h = g.be16(), g.be16()
    g.p += max(0, n - 11)
    return fmt, w, h


def five(v):
    return (v << 3) | (v >> 2)


def parse(data):
    g = In(data)
    if [g.be32() for _ in range(4)] != [0x12, 0x08, 0x02, 0x02]:
        raise PixError("not a BRender pixel map")
    t = g.be32()
    if t != 0x03:
        raise PixError("bad chunk type")
    fmt, w, h = header(g)
    if fmt not in FORMATS:
        raise PixError("unsupported pixel format")
    name, bpp = FORMATS[fmt]
    bpl = bpp * w
    if not w or not h:
        raise PixError("empty picture")
    t = g.be32()
    pal = None
    if name == "pal8" and t in (0x03, 0x3D):
        pfmt, _, _ = header(g)
        if pfmt != 7:
            raise PixError("palette is not RGB")
        t = g.be32()
        n = g.be32()
        if t != 0x21:
            raise PixError("bad palette chunk")
        pal = [g.be32() & 0xFFFFFF for _ in range(256)]
        g.p += 16
        t = g.be32()
    elif name == "pal8":
        pal = [k * 0x010101 for k in range(256)]
    n = g.be32()
    left = g.left()
    if t != 0x21 or left // bpl < h:
        raise PixError("bad image data chunk")
    raw = data[g.p:g.p + h * bpl]
    rgb, alpha = bytearray(), None
    if name in ("argb", "ya8"):
        alpha = bytearray()
    for k in range(w * h):
        px = raw[k * bpp:(k + 1) * bpp]
        if name == "pal8":
            v = pal[px[0]]
            rgb += bytes((v >> 16 & 0xFF, v >> 8 & 0xFF, v & 0xFF))
        elif name == "rgb555":
            v = px[0] << 8 | px[1]
            rgb += bytes((five(v >> 10 & 31), five(v >> 5 & 31), five(v & 31)))
        elif name == "rgb565":
            v = px[0] << 8 | px[1]
            g6 = v >> 5 & 63
            rgb += bytes((five(v >> 11 & 31), (g6 << 2) | (g6 >> 4), five(v & 31)))
        elif name == "rgb24":
            rgb += px
        elif name in ("xrgb", "argb"):
            rgb += px[0:3]
        else:
            rgb += bytes((px[0],) * 3)
            alpha.append(px[1])
    return {"format": name, "width": w, "height": h, "crc32": "%08x" % zlib.crc32(bytes(rgb)),
            "alpha_crc32": None if alpha is None else "%08x" % zlib.crc32(bytes(alpha))}
