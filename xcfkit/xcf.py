"""GIMP XCF files (versions file, v001-v003): image header, properties, layers and their tile data."""
import struct
import zlib

TYPES = {0: ("RGB", 3), 1: ("RGBA", 4), 2: ("GRAY", 1), 3: ("GRAYA", 2), 4: ("INDEXED", 1), 5: ("INDEXEDA", 2)}
BASES = {0: "RGB", 1: "GRAY", 2: "INDEXED"}
COMP = {0: "none", 1: "rle"}


class XcfError(ValueError):
    pass


class _R:
    def __init__(self, data, pos=0):
        self.d, self.p = data, pos

    def u32(self):
        if self.p + 4 > len(self.d):
            raise XcfError("file is truncated")
        v = struct.unpack_from(">I", self.d, self.p)[0]
        self.p += 4
        return v

    def i32(self):
        v = self.u32()
        return v - (1 << 32) if v >= 1 << 31 else v

    def take(self, n):
        if self.p + n > len(self.d):
            raise XcfError("file is truncated")
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def props(self):
        out = {}
        while True:
            t, n = self.u32(), self.u32()
            if t == 0:
                return out
            out.setdefault(t, self.take(n))


def _rle(src, pos, count):
    out = bytearray()
    while len(out) < count:
        v = src[pos]
        pos += 1
        if v >= 128:
            n = v - 127
            out += src[pos:pos + n]
            pos += n
        else:
            n = v + 1
            out += bytes([src[pos]]) * n
            pos += 1
    if len(out) != count:
        raise XcfError("RLE data overruns the tile")
    return bytes(out), pos


def _tile(data, ptr, w, h, bpp, comp):
    n = w * h
    if comp == 0:
        return data[ptr:ptr + n * bpp]
    out, _ = _rle(data, ptr, n * bpp)
    return out


def parse(data):
    if data[:9] != b"gimp xcf " or len(data) < 26 or data[9:14] not in (b"file\0", b"v001\0", b"v002\0", b"v003\0"):
        raise XcfError("not a GIMP XCF file (versions file to v003)")
    r = _R(data, 14)
    w, h, base = r.u32(), r.u32(), r.u32()
    props = r.props()
    comp = props.get(17, b"\0")[0]
    if comp not in COMP:
        raise XcfError("unsupported compression %d" % comp)
    cmap = props.get(1)
    ncol = struct.unpack_from(">I", cmap)[0] if cmap else 0
    ptrs = []
    while True:
        p = r.u32()
        if not p:
            break
        ptrs.append(p)
    layers = []
    for p in ptrs:
        lr = _R(data, p)
        lw, lh, lt = lr.u32(), lr.u32(), lr.u32()
        n = lr.u32()
        nm = lr.take(n).decode("utf-8")
        lp = lr.props()
        hptr = lr.u32()
        hr = _R(data, hptr)
        hw, hh, bpp = hr.u32(), hr.u32(), hr.u32()
        lv = _R(data, hr.u32())
        vw, vh = lv.u32(), lv.u32()
        tname, tbpp = TYPES[lt]
        pix = bytearray(vw * vh * bpp)
        k = 0
        for ty in range(0, vh, 64):
            for tx in range(0, vw, 64):
                tw, th = 64, 64
                tp = lv.u32()
                if not tp:
                    raise XcfError("missing tile")
                t = _tile(data, tp, tw, th, bpp, comp)
                if len(t) < tw * th * bpp:
                    raise XcfError("tile data is truncated")
                for yy in range(min(th, vh - ty)):
                    row = ((ty + yy) * vw + tx) * bpp
                    cw = min(tw, vw - tx)
                    pix[row:row + cw * bpp] = t[yy * tw * bpp:yy * tw * bpp + cw * bpp]
                k += 1
        x, y = struct.unpack(">II", lp[15]) if 15 in lp else (0, 0)
        alpha = tname.endswith("A")
        layers.append({"name": nm, "type": tname, "width": lw, "height": lh, "x": x, "y": y,
                       "opacity": struct.unpack(">I", lp[6])[0] if 6 in lp else 255,
                       "visible": bool(struct.unpack(">I", lp[8])[0]) if 8 in lp else True,
                       "first_pixel": pix[:bpp].hex(), "last_pixel": pix[-bpp:].hex() if pix else "",
                       "clear_pixels": sum(1 for i in range(bpp - 1, len(pix), bpp) if pix[i] == 0) if alpha else None,
                       "crc32": "%08x" % zlib.crc32(bytes(pix))})
    return {"width": w, "height": h, "base_type": BASES.get(base, base), "compression": COMP[comp],
            "colormap_colors": ncol, "layers": layers}
