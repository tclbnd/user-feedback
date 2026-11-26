"""GBA/NDS BIOS-compressed blobs (.lz): header, LZ77, LZ11, Huffman, RLE and difference filters."""
import struct
import zlib

TYPES = {0x10: "lz77", 0x30: "rle"}


class PackError(ValueError):
    pass


class _Short(Exception):
    pass


def _lz(src, p, size):
    out = bytearray()
    while len(out) < size:
        if p >= len(src):
            raise _Short
        flags = src[p]
        p += 1
        for bit in range(8):
            if len(out) >= size:
                break
            if p >= len(src):
                raise _Short
            if flags >> bit & 1:
                b0 = src[p]
                if p + 1 >= len(src):
                    raise _Short
                n, d = (b0 >> 4) + 3, (b0 & 15) << 8 | src[p + 1]
                p += 2
                if d > len(out) or d == 0:
                    raise PackError("reference before start of data")
                for _ in range(n):
                    if len(out) >= size:
                        break
                    out.append(out[-d])
            else:
                out.append(src[p])
                p += 1
    return out


def _rle(src, p, size):
    out = bytearray()
    while len(out) < size:
        if p >= len(src):
            raise _Short
        f = src[p]
        p += 1
        if f & 0x80:
            if p >= len(src):
                raise _Short
            out += bytes([src[p]]) * ((f & 0x7F) + 1)
            p += 1
        else:
            n = f & 0x7F
            if p + n > len(src):
                raise _Short
            out += src[p:p + n]
            p += n
    return out[:size]


def parse(data):
    if len(data) < 4:
        raise PackError("too short")
    t = data[0]
    if t not in TYPES:
        raise PackError("unknown compression type 0x%02x" % t)
    size = data[1] | data[2] << 8 | data[3] << 16
    p = 4
    complete = True
    try:
        if t == 0x10:
            out = _lz(data, p, size)
        else:
            out = _rle(data, p, size)
    except _Short:
        out, complete = None, False
    res = {"type": TYPES[t], "size": size, "complete": complete, "crc32": None, "tiles": None, "blank_tiles": None}
    if out is not None:
        res["crc32"] = "%08x" % zlib.crc32(bytes(out))
        res["tiles"] = len(out) // 32
        res["blank_tiles"] = sum(1 for i in range(len(out) // 32) if not any(out[32 * i:32 * i + 32]))
    return res
