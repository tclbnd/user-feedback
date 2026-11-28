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
    """Flag byte, least significant bit first: 1 copies a literal byte, 0 reads a
    little-endian back reference.  Its high 12 bits hold the distance - 1 and the
    low 4 bits the run length - 2 (so overlapping copies repeat the pattern)."""
    out = bytearray(size)
    pos = 0
    i, end = 0, len(src)
    while i < end and pos < size:
        code = src[i]
        i += 1
        for b in range(8):
            if i >= end or pos >= size:
                break
            if code & (1 << b):
                out[pos] = src[i]
                pos += 1
                i += 1
            else:
                if i + 1 >= end:
                    raise DsvError("lzss reference is truncated")
                cmd = src[i] | (src[i + 1] << 8)
                i += 2
                distance = (cmd >> 4) + 1
                if distance > pos:
                    raise DsvError("lzss reference before start")
                for _ in range((cmd & 15) + 2):
                    out[pos] = out[pos - distance]
                    pos += 1
                    if pos >= size:
                        break
    return bytes(out)


def rle(src, size):
    """Code byte: high bit set repeats the next byte (code & 0x7F) + 1 times,
    otherwise copies code + 1 bytes.  Delta frames only encode the pixels that
    changed, so everything left over stays zero."""
    out = bytearray(size)
    pos = 0
    i, end = 0, len(src)
    while i < end and pos < size:
        code = src[i]
        i += 1
        if code & 0x80:
            if i >= end:
                break
            n = (code & 0x7F) + 1
            k = min(n, size - pos)
            out[pos:pos + k] = bytes([src[i]]) * k
            i += 1
        else:
            n = code + 1
            k = min(n, size - pos, end - i)
            out[pos:pos + k] = src[i:i + k]
            i += n
        pos += n
    return bytes(out)


def bitmap(ftype, src, size):
    """Decode one bitmap packet into colour indices.  Delta types yield the
    difference to the previous frame and still need ``apply_delta``."""
    if ftype in (9, 34):
        return rle(src, size)
    if ftype in (35, 36):
        # Here Huffman expands the still compressed RLE stream, which can be
        # longer than the picture itself, so only bound it by what the input can
        # yield: every consumed byte produces at most two output bytes.
        return rle(huffman(src, 2 * len(src)), size)
    if ftype == 37:
        out = huffman(src, size)
        if len(out) < size - 95 * size // 100:
            raise DsvError("huffman data is too short")
        return out
    if ftype in (38, 39):
        return lzss(src, size)
    raise DsvError("unknown bitmap type %d" % ftype)


def apply_delta(delta, cur):
    """Delta packets store the difference to the previous frame; the player adds
    them, so pixels the packet does not mention (zero) stay unchanged."""
    return bytes((a + b) & 0xFF for a, b in zip(cur, delta))


def parse(data):
    if data[:8] != b"DSVPACK1":
        raise DsvError("not a video pack")
    w, h, count = struct.unpack_from("<HHH", data, 8)
    if not w or not h:
        raise DsvError("empty picture")
    size = w * h
    cur = bytes(size)
    pal = [0] * 256
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
        try:
            stride = 4 if ptype else 3
            if ncol > 256:
                raise DsvError("too many colours")
            if len(body) < ncol * stride:
                raise DsvError("palette is truncated")
            # The palette is global to the movie: frames that carry no colours
            # keep the palette of the previous frame.
            for k in range(ncol):
                pal[k] = body[stride * k] << 16 | body[stride * k + 1] << 8 | body[stride * k + 2]
            body = body[stride * ncol:]
            new = bitmap(ftype, body, size)
            if ftype == 37:
                # A plain Huffman packet only carries the top part of the
                # picture; the rest keeps the previous frame.
                new += cur[len(new):]
            elif ftype in (34, 36, 39):
                new = apply_delta(new, cur)
            cur = new
            # The player stores the bitmap bottom row first.
            img = b"".join(bytes((pal[v] >> 16 & 0xFF, pal[v] >> 8 & 0xFF, pal[v] & 0xFF))
                           for y in range(h - 1, -1, -1) for v in new[y * w:(y + 1) * w])
            frames.append({"state": "decoded", "crc32": "%08x" % zlib.crc32(img)})
        except DsvError:
            frames.append({"state": "error", "crc32": None})
    return {"width": w, "height": h, "frames": frames}
