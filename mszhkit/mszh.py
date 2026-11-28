"""LCL MSZH video frames (Avimszh) from a rip pack: MSZH decompression and the LCL image layouts."""
import struct
import zlib

TYPES = {0: "yuv111", 1: "yuv422", 2: "rgb24", 3: "yuv411", 4: "yuv211", 5: "yuv420"}


class MszhError(ValueError):
    pass


def decomp(src, size):
    """MSZH: a mask byte, high bit first; 0 copies four literal bytes, 1 reads a little-endian
    word with the length in the top five bits and the distance in the low eleven."""
    out = bytearray()
    if not src:
        return out
    i = 1
    mask, bit = src[0], 0x80
    end = len(src)
    while i < end and len(out) < size:
        if not mask & bit:
            out += src[i:i + 4].ljust(4, b"\0")
            i += 4
        else:
            v = src[i] | (src[i + 1] << 8 if i + 1 < end else 0)
            i += 2
            cnt = (v >> 11) + 1
            ofs = min(v & 0x7FF, len(out))
            cnt = min(cnt, size - len(out))
            if ofs:
                start = len(out) - ofs
                out += out[start:start + cnt]
            else:
                out += bytes(cnt)
        bit >>= 1
        if not bit:
            mask = src[i] if i < end else 0
            i += 1
            while not mask:
                if size - len(out) < 32 or end - i < 32:
                    break
                out += src[i:i + 32]
                i += 32
                mask = src[i] if i < end else 0
                i += 1
            bit = 0x80
    return out


def layout(itype, w, h):
    if itype == 0:
        return w * h * 3
    if itype == 1:
        return (w & ~3) * h * 2
    if itype == 2:
        return ((w * 3 + 3) & ~3) * h
    if itype == 3:
        return (w & ~3) * h // 2 * 3
    if itype == 4:
        return w * h * 2
    return w * h // 2 * 3


def convert(itype, w, h, enc):
    """Convert the stored samples to planes."""
    p = iter(enc)
    nxt = lambda: next(p, 0)
    if itype == 2:
        stride = w * 3
        rows = []
        for r in range(h):
            rows.append(enc[r * stride:r * stride + 3 * w])
        return b"".join(rows)
    cw = {0: w, 1: (w + 1) // 2, 3: (w + 3) // 4, 4: (w + 1) // 2, 5: (w + 1) // 2}[itype]
    ch = (h + 1) // 2 if itype == 5 else h
    Y = [bytearray(w) for _ in range(h)]
    U = [bytearray(cw) for _ in range(ch)]
    V = [bytearray(cw) for _ in range(ch)]
    c8 = nxt
    if itype == 5:
        for r in range(0, h - 1, 2):
            y0, y1, cr = r, r + 1, r // 2
            for col in range(0, w - 1, 2):
                Y[y0][col], Y[y1][col] = nxt(), nxt()
                Y[y0][col + 1], Y[y1][col + 1] = nxt(), nxt()
                U[cr][col >> 1] = c8()
                V[cr][col >> 1] = c8()
    else:
        for r in range(h):
            y = r
            if itype == 0:
                for col in range(w):
                    Y[y][col] = nxt()
                    U[y][col] = c8()
                    V[y][col] = c8()
            elif itype == 4:
                for col in range(0, w - 1, 2):
                    Y[y][col], Y[y][col + 1] = nxt(), nxt()
                    U[y][col >> 1] = c8()
                    V[y][col >> 1] = c8()
            else:
                col = 0
                while col < w - 3:
                    for k in range(4):
                        Y[y][col + k] = nxt()
                    if itype == 1:
                        U[y][col >> 1], U[y][(col >> 1) + 1] = c8(), c8()
                        V[y][col >> 1], V[y][(col >> 1) + 1] = c8(), c8()
                    else:
                        U[y][col >> 2] = c8()
                        V[y][col >> 2] = c8()
                    col += 4
    return b"".join(bytes(r) for r in Y + U + V)


def parse(data):
    if data[:8] != b"MSZHPACK":
        raise MszhError("not an MSZH pack")
    w, h = struct.unpack_from("<HH", data, 8)
    extra = data[12:20]
    count, = struct.unpack_from("<H", data, 20)
    if len(extra) < 8 or extra[7] != 1:
        raise MszhError("not an MSZH stream")
    itype, comp, flags = extra[4], extra[5], extra[6]
    if itype not in TYPES:
        raise MszhError("unsupported image type")
    if comp != 0:
        raise MszhError("unsupported compression")
    if not w or not h:
        raise MszhError("empty picture")
    if itype == 5 and (h & 1 or w & 1) or itype in (4,) and w & 1:
        raise MszhError("unsupported dimensions")
    size = layout(itype, w, h)
    pos = 22
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise MszhError("frame table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        buf = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(buf) < n:
            raise MszhError("frame is truncated")
        try:
            enc = decomp(buf, size)
            if len(enc) < size:
                raise MszhError("decoded size differs")
            enc = bytes(enc[:size])
            pic = convert(itype, w, h, enc)
            frames.append({"state": "decoded", "crc32": "%08x" % zlib.crc32(pic)})
        except MszhError:
            frames.append({"state": "error", "crc32": None})
    return {"width": w, "height": h, "type": TYPES[itype], "frames": frames}
