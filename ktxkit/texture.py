"""KTX 1.1 textures (.ktx): header, key/value data, mip levels and texels."""
import struct

ID = b"\xabKTX 11\xbb\r\n\x1a\n"
UBYTE, RGB, RGBA = 0x1401, 0x1907, 0x1908


class KtxError(ValueError):
    pass


def parse(data):
    if data[:12] != ID or len(data) < 64:
        raise KtxError("not a KTX 1.1 file")
    (_, gltype, _, glformat, internal, _, w, h, d, arr, faces, mips, kvlen) = struct.unpack_from("<13I", data, 12)
    if w == 0 or faces not in (1, 6):
        raise KtxError("unsupported texture")
    kv = {}
    p, end = 64, 64 + kvlen
    while p + 4 <= end:
        n = struct.unpack_from("<I", data, p)[0]
        blob = data[p + 4:p + 4 + n]
        p += 4 + n
        k, _, v = blob.partition(b"\0")
        if v.endswith(b"\0"):
            v = v[:-1]
        kv[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
    bpp = {RGB: 3, RGBA: 4}.get(glformat) if gltype == UBYTE else None
    levels, first, last, smallest, complete = [], [], None, None, True

    def texel(off):
        if bpp is None or off + bpp > len(data):
            return None
        t = data[off:off + bpp] + (b"\xff" if bpp == 3 else b"")
        return "#%02x%02x%02x%02x" % tuple(t)

    if p + 4 > len(data):
        complete = False
    else:
        size = struct.unpack_from("<I", data, p)[0]
        p += 4
        for L in range(mips):
            lw, lh, ld = max(1, w >> L), max(1, h >> L), max(1, d >> L)
            if L:
                size = lw * lh * ld * (bpp or 0) * max(1, arr) * faces
            levels.append([lw, lh, size])
            count = max(1, arr) * faces * ld
            one = size // count if count else 0
            bases = [p + i * one for i in range(count)]
            p += size
            if p > len(data):
                complete = False
            row = lw * (bpp or 0)
            if L == 0:
                first = [texel(b) for b in bases[:8]]
                last = texel(bases[-1] + (lh - 1) * row + (lw - 1) * (bpp or 0))
            smallest = texel(bases[0])
    return {"endianness": "little", "gl_type": gltype, "gl_format": glformat,
            "gl_internal_format": internal, "width": w, "height": h, "depth": d, "array_elements": arr,
            "faces": faces, "mip_levels": mips, "key_values": kv, "levels": levels, "complete": complete,
            "first_texels": first, "last_texel": last, "smallest_texel": smallest}
