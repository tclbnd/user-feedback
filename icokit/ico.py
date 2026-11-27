"""Windows icon and cursor files (.ico, .cur): directory entries, BMP (DIB) images with AND masks, PNG images."""
import struct
import zlib


class IcoError(ValueError):
    pass


def _row(nbytes):
    return (nbytes + 3) & ~3


def dib(data, off, size):
    if off + 40 > len(data) or size < 40:
        raise IcoError("bitmap header is truncated")
    hs, w, h2, planes, bpp, comp, _, _, _, used, _ = struct.unpack_from("<IiiHHIIiiII", data, off)
    if hs < 40:
        raise IcoError("unknown bitmap header")
    if comp != 0:
        raise IcoError("compressed bitmaps are not supported")
    if bpp not in (1, 4, 8, 24, 32):
        raise IcoError("unsupported bit depth %d" % bpp)
    h = h2 // 2
    if w <= 0 or h <= 0:
        raise IcoError("bad bitmap size")
    p = off + hs
    pal = []
    if bpp <= 8:
        n = used or (1 << bpp)
        if p + 4 * n > len(data):
            raise IcoError("palette is truncated")
        pal = [(data[p + 4 * k + 2], data[p + 4 * k + 1], data[p + 4 * k]) for k in range(n)]
        p += 4 * n
    xr = (w * bpp + 7) // 8
    mr = (w + 7) // 8
    need = xr * h + (mr * h if bpp != 32 else 0)
    if p + xr * h > len(data):
        raise IcoError("image data is truncated")
    rows = []
    mask_at = p + xr * h
    for y in range(h):
        src = p + y * xr
        mrow = mask_at + y * mr
        out = bytearray()
        for x in range(w):
            if bpp == 32:
                b, g, r, a = data[src + 4 * x:src + 4 * x + 4]
            else:
                if bpp == 24:
                    b, g, r = data[src + 3 * x:src + 3 * x + 3]
                else:
                    bit = x * bpp
                    v = (data[src + (bit >> 3)] >> (bit & 7)) & ((1 << bpp) - 1)
                    r, g, b = pal[v] if v < len(pal) else (0, 0, 0)
                a = 255
            out += bytes((r, g, b, a))
        rows.append(bytes(out))
    return w, h, bpp, rows


def parse(data):
    if len(data) < 6:
        raise IcoError("directory is truncated")
    res, kind, count = struct.unpack_from("<HHH", data, 0)
    if res != 0 or kind != 1:
        raise IcoError("not an icon file")
    if 6 + 16 * count > len(data):
        raise IcoError("directory entries are truncated")
    images = []
    for k in range(count):
        dw, dh, ncol, _, f1, f2, size, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * k)
        img = {"width": dw or 256, "height": dh or 256}
        if off + size > len(data):
            raise IcoError("image %d lies outside the file" % k)
        if data[off:off + 8] == b"\x89PNG\r\n\x1a\n":
            iw, ih = struct.unpack_from(">II", data, off + 16)
            img.update(format="png", width=iw, height=ih, crc32=None, first_row=None, transparent=None)
        else:
            w, h, bpp, rows = dib(data, off, size)
            raw = b"".join(rows)
            img.update(format="bmp", width=w, height=h, bits=bpp, crc32="%08x" % zlib.crc32(raw),
                       first_row="%08x" % zlib.crc32(rows[0]), transparent=sum(1 for i in range(3, len(raw), 4) if raw[i] == 0))
        images.append(img)
    return {"type": "cursor" if kind == 2 else "icon", "images": images}
