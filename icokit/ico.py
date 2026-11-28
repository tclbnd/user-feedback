"""Windows icon and cursor files (.ico, .cur): directory entries, BMP (DIB) images with AND masks, PNG images."""
import struct
import zlib


class IcoError(ValueError):
    pass


def _row(nbytes):
    return (nbytes + 3) & ~3


def dib(data, off, size):
    # Prefer the directory entry's image length so a missing mask cannot read
    # into the next image, but fall back to the end of the file for files whose
    # entry size is understated (common in old icon resources).
    if 0 < size <= len(data) - off:
        end = off + size
    else:
        end = len(data)
    if off + 40 > end:
        raise IcoError("bitmap header is truncated")
    hs, w, h2, planes, bpp, comp, _, _, _, used, _ = struct.unpack_from("<IiiHHIIiiII", data, off)
    if hs < 40 or off + hs > end:
        raise IcoError("unknown bitmap header")
    if comp != 0:
        raise IcoError("compressed bitmaps are not supported")
    if bpp not in (1, 4, 8, 24, 32):
        raise IcoError("unsupported bit depth %d" % bpp)
    # The DIB stores XOR (colour) rows above AND (mask) rows; both are counted
    # in the doubled height field.
    h = h2 // 2
    if w <= 0 or h <= 0:
        raise IcoError("bad bitmap size")
    p = off + hs
    pal = []
    if bpp <= 8:
        n = used or (1 << bpp)
        if p + 4 * n > end:
            raise IcoError("palette is truncated")
        pal = [(data[p + 4 * k + 2], data[p + 4 * k + 1], data[p + 4 * k]) for k in range(n)]
        p += 4 * n
    # DIB rows are DWORD aligned, including the 1bpp/4bpp colour rows and the
    # AND mask rows. The mask is optional in practice (and absent from most
    # 32bpp images), so accept what is actually there.
    xr = _row((w * bpp + 7) // 8)
    mr = _row((w + 7) // 8)
    if p + xr * h > end:
        raise IcoError("image data is truncated")
    mask_at = p + xr * h
    # The AND mask is optional in practice: most 32bpp images rely on their
    # alpha channel instead, and a few old files leave it out altogether.
    mask_end = mask_at + mr * h
    has_mask = mask_end <= end
    if not has_mask and bpp != 32 and mask_end <= len(data):
        # The directory entry understated the image length; the mask is there.
        has_mask = True
    rows = []
    masks = []
    # DIB rows are bottom-up; emit them top-down the way Windows draws them.
    for y in range(h):
        src = p + (h - 1 - y) * xr
        out = bytearray()
        for x in range(w):
            if bpp == 32:
                b, g, r, a = data[src + 4 * x:src + 4 * x + 4]
            elif bpp == 24:
                b, g, r = data[src + 3 * x:src + 3 * x + 3]
                a = 255
            else:
                # Packed pixels run from the most significant bit down.
                bit = x * bpp
                v = (data[src + (bit >> 3)] >> (8 - bpp - (bit & 7))) & ((1 << bpp) - 1)
                r, g, b = pal[v] if v < len(pal) else (0, 0, 0)
                a = 255
            out += bytes((r, g, b, a))
        rows.append(bytes(out))
        if has_mask:
            mrow = mask_at + (h - 1 - y) * mr
            bits = bytearray(w)
            for x in range(w):
                bits[x] = (data[mrow + (x >> 3)] >> (7 - (x & 7))) & 1
            masks.append(bytes(bits))

    if bpp == 32:
        # A 32bpp image with a real alpha channel: alpha alone decides
        # transparency and the AND mask is ignored. If every alpha byte is 0
        # the file does not use the channel, so fall back to the mask, or to
        # fully opaque when there is no mask either.
        if any(row[i] for row in rows for i in range(3, len(row), 4)):
            for y, row in enumerate(rows):
                row = bytearray(row)
                for i in range(3, len(row), 4):
                    row[i] = 255 if row[i] else 0
                rows[y] = bytes(row)
        elif masks:
            for y, row in enumerate(rows):
                row = bytearray(row)
                mask = masks[y]
                for x in range(w):
                    row[4 * x + 3] = 0 if mask[x] else 255
                rows[y] = bytes(row)
        else:
            for y, row in enumerate(rows):
                row = bytearray(row)
                for i in range(3, len(row), 4):
                    row[i] = 255
                rows[y] = bytes(row)
    elif masks:
        # 1/4/8/24bpp: the AND mask marks transparent pixels.
        for y, row in enumerate(rows):
            row = bytearray(row)
            mask = masks[y]
            for x in range(w):
                row[4 * x + 3] = 0 if mask[x] else 255
            rows[y] = bytes(row)
    return w, h, bpp, rows


def parse(data):
    if len(data) < 6:
        raise IcoError("directory is truncated")
    res, kind, count = struct.unpack_from("<HHH", data, 0)
    if res != 0 or kind not in (1, 2):
        raise IcoError("not an icon file")
    if 6 + 16 * count > len(data):
        raise IcoError("directory entries are truncated")
    cursor = kind == 2
    images = []
    for k in range(count):
        dw, dh, ncol, _, f1, f2, size, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * k)
        if off + size > len(data):
            raise IcoError("image %d lies outside the file" % k)
        if data[off:off + 8] == b"\x89PNG\r\n\x1a\n":
            if off + 24 > len(data):
                raise IcoError("image %d is truncated" % k)
            iw, ih = struct.unpack_from(">II", data, off + 16)
            img = {"width": iw, "height": ih, "format": "png"}
            if cursor:
                # Cursor entries reuse the colour-count and plane fields as the
                # hotspot location.
                img["hotspot"] = [f1, f2]
            img.update(crc32=None, first_row=None, transparent=None)
        else:
            w, h, bpp, rows = dib(data, off, size)
            raw = b"".join(rows)
            img = {"width": w, "height": h, "format": "bmp"}
            if cursor:
                img["hotspot"] = [f1, f2]
            img.update(bits=bpp, crc32="%08x" % zlib.crc32(raw),
                       first_row="%08x" % zlib.crc32(rows[0]),
                       transparent=sum(1 for i in range(3, len(raw), 4) if raw[i] == 0))
        images.append(img)
    return {"type": "cursor" if cursor else "icon", "images": images}
