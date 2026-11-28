"""Delphine Software cut-scene video packets: palette updates, RLE, LZSS and nibble Huffman bitmaps, delta frames."""
import struct
import zlib


class DsvError(ValueError):
    pass


def huffman(src, size):
    """15-byte code table; each byte holds two 4-bit codes; code 15 escapes to a literal byte."""
    if len(src) < 15:
        raise DsvError("huffman table is truncated")
    table = src[:15]
    i, end = 15, len(src)
    out = bytearray()
    get = lambda k: src[k] if k < end else 0
    while i < end:
        code = get(i)
        i += 1
        if code >> 4 == 15:
            out.append(get(i))
            i += 1
        else:
            out.append(table[code >> 4])
        if len(out) >= size:
            break
        code &= 15
        if code == 15:
            out.append(get(i))
            i += 1
        else:
            out.append(table[code])
        if len(out) >= size:
            break
    return bytes(out)


def lzss(src, size):
    """Flag byte: 1 copies a literal byte, 0 reads a little-endian back reference."""
    out = bytearray()
    i, end = 0, len(src)
    while i < end and len(out) < size:
        code = src[i]
        i += 1
        for b in range(8):
            if not (i < end and len(out) < size):
                break
            if code & (0x80 >> b):
                out.append(src[i])
                i += 1
            else:
                cmd = src[i] | (src[i + 1] << 8 if i + 1 < end else 0)
                i += 2
                offset = cmd >> 4
                if len(out) < offset or not offset:
                    raise DsvError("lzss reference before start")
                n = min((cmd & 15) + 2, size - len(out))
                start = len(out) - offset
                out += out[start:start + n]
    return bytes(out) + bytes(size - len(out))


def rle(src, size, base):
    """Code byte: high bit set repeats the next byte, otherwise copies code + 1 bytes."""
    out = bytearray(size)
    pos = 0
    i, end = 0, len(src)
    while i + 1 < end and pos < size:
        code = src[i]
        i += 1
        if code & 0x80:
            n = code & 0x7F
            k = min(n, size - pos)
            out[pos:pos + k] = bytes([src[i]]) * k
            i += 1
        else:
            n = code + 1
            k = min(n, size - pos)
            out[pos:pos + k] = src[i:i + k]
            i += n
        pos += n
    return bytes(out)


def parse(data):
    if data[:8] != b"DSVPACK1":
        raise DsvError("not a video pack")
    w, h, count = struct.unpack_from("<HHH", data, 8)
    if not w or not h:
        raise DsvError("empty picture")
    size = w * h
    cur, prev = bytes(size), bytes(size)
    pos = 14
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise DsvError("frame table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        pkt = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(pkt) < n or n < 4:
            raise DsvError("frame is truncated")
        ptype, ncol, ftype = pkt[0], pkt[1] | pkt[2] << 8, pkt[3]
        body = pkt[4:]
        pal = [0] * 256
        try:
            if len(body) < ncol * (3 + (ptype != 0)):
                raise DsvError("palette is truncated")
            if ncol > 256:
                raise DsvError("too many colours")
            for k in range(ncol):
                pal[k] = body[3 * k] << 16 | body[3 * k + 1] << 8 | body[3 * k + 2]
            body = body[3 * ncol:]
            new = cur
            if ftype in (9, 34):
                new = rle(body, size, cur)
            elif ftype in (35, 36):
                new = rle(huffman(body, size), size, cur)
            elif ftype == 37:
                hb = huffman(body, size)
                if len(hb) < size - 95 * size // 100:
                    raise DsvError("huffman data is too short")
                new = hb + cur[len(hb):]
            elif ftype in (38, 39):
                new = lzss(body, size)
            cur, prev = prev, new
            img = b"".join(bytes((pal[v] >> 16 & 0xFF, pal[v] >> 8 & 0xFF, pal[v] & 0xFF))
                           for y in range(h) for v in new[y * w:(y + 1) * w])
            frames.append({"state": "decoded", "crc32": "%08x" % zlib.crc32(img)})
        except DsvError:
            frames.append({"state": "error", "crc32": None})
    return {"width": w, "height": h, "frames": frames}
