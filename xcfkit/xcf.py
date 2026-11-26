"""GIMP XCF files (versions file, v001-v003): image header, properties, layers and their tile data."""
import struct
import zlib

TYPES = {0: ("RGB", 3), 1: ("RGBA", 4), 2: ("GRAY", 1), 3: ("GRAYA", 2), 4: ("INDEXED", 1), 5: ("INDEXEDA", 2)}
BASES = {0: "RGB", 1: "GRAY", 2: "INDEXED"}
COMP = {0: "none", 1: "rle", 2: "zlib"}
TILE = 64


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


def _rle_short(src, pos, count):
    """Decode the one-byte count form of XCF's byte-plane RLE."""
    out = bytearray()
    nsrc = len(src)
    while len(out) < count:
        if pos >= nsrc:
            raise XcfError("RLE data is out of bounds")
        tag = src[pos]
        pos += 1
        if tag >= 128:
            n = tag - 127
            if pos + n > nsrc:
                raise XcfError("RLE data is out of bounds")
            out.extend(src[pos:pos + n])
            pos += n
        else:
            n = tag + 1
            if pos >= nsrc:
                raise XcfError("RLE data is out of bounds")
            value = src[pos]
            pos += 1
            out.extend(bytes((value,)) * n)

    if len(out) != count:
        raise XcfError("RLE data overruns the tile")
    return bytes(out), pos


def _rle_long(src, pos, count):
    """Decode RLE with big-endian uint16 escape counts for long runs."""
    out = bytearray()
    nsrc = len(src)

    def need(n):
        if pos + n > nsrc:
            raise XcfError("RLE data is out of bounds")

    while len(out) < count:
        need(1)
        tag = src[pos]
        pos += 1

        if tag == 127:
            need(3)
            n = struct.unpack_from(">H", src, pos)[0]
            value = src[pos + 2]
            pos += 3
            out.extend(bytes((value,)) * n)
        elif tag == 128:
            need(2)
            n = struct.unpack_from(">H", src, pos)[0]
            pos += 2
            need(n)
            out.extend(src[pos:pos + n])
            pos += n
        elif tag < 128:
            n = tag + 1
            need(1)
            value = src[pos]
            pos += 1
            out.extend(bytes((value,)) * n)
        else:
            n = tag - 127
            need(n)
            out.extend(src[pos:pos + n])
            pos += n

    if len(out) != count:
        raise XcfError("RLE data overruns the tile")
    return bytes(out), pos


def _rle(src, pos, count):
    try:
        return _rle_short(src, pos, count)
    except XcfError:
        return _rle_long(src, pos, count)


def _plane_to_pixels(plane, w, h, bpp):
    n = w * h
    pixels = bytearray(n * bpp)
    for channel in range(bpp):
        start = channel * n
        pixels[channel::bpp] = plane[start:start + n]
    return bytes(pixels)


def _tile(data, ptr, w, h, bpp, comp):
    n = w * h
    count = n * bpp
    if comp == 0:
        if ptr + count > len(data):
            raise XcfError("tile data is truncated")
        return data[ptr:ptr + count]
    if comp == 2:
        try:
            raw = zlib.decompress(data[ptr:])
        except zlib.error as exc:
            raise XcfError("invalid zlib tile data") from exc
        if len(raw) != count:
            raise XcfError("zlib tile has the wrong size")
        return raw

    plane, _ = _rle(data, ptr, count)
    return _plane_to_pixels(plane, w, h, bpp)


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
        nm = lr.take(n).rstrip(b"\0").decode("utf-8")
        lp = lr.props()
        hptr = lr.u32()
        hr = _R(data, hptr)
        hw, hh, bpp = hr.u32(), hr.u32(), hr.u32()
        lv = _R(data, hr.u32())
        vw, vh = lv.u32(), lv.u32()
        tname, tbpp = TYPES[lt]
        pix = bytearray(vw * vh * bpp)
        for ty in range(0, vh, TILE):
            for tx in range(0, vw, TILE):
                tw = min(TILE, vw - tx)
                th = min(TILE, vh - ty)
                tp = lv.u32()
                if not tp:
                    raise XcfError("missing tile")
                t = _tile(data, tp, tw, th, bpp, comp)
                if len(t) < tw * th * bpp:
                    raise XcfError("tile data is truncated")
                for yy in range(th):
                    row = ((ty + yy) * vw + tx) * bpp
                    pix[row:row + tw * bpp] = t[yy * tw * bpp:(yy + 1) * tw * bpp]
        x, y = struct.unpack(">ii", lp[15]) if 15 in lp else (0, 0)
        alpha = tname.endswith("A")
        layers.append({"name": nm, "type": tname, "width": lw, "height": lh, "x": x, "y": y,
                       "opacity": struct.unpack(">I", lp[6])[0] if 6 in lp else 255,
                       "visible": bool(struct.unpack(">I", lp[8])[0]) if 8 in lp else True,
                       "first_pixel": pix[:bpp].hex(), "last_pixel": pix[-bpp:].hex() if pix else "",
                       "clear_pixels": sum(1 for i in range(bpp - 1, len(pix), bpp) if pix[i] == 0) if alpha else None,
                       "crc32": "%08x" % zlib.crc32(bytes(pix))})
    return {"width": w, "height": h, "base_type": BASES.get(base, base), "compression": COMP[comp],
            "colormap_colors": ncol, "layers": layers}
