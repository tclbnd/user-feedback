"""Westwood WSA animations (.wsa): frame offsets, LCW (format 80) packed XOR deltas (format 40), optional palette."""
import zlib


class WsaError(ValueError):
    pass


def _u16(d, p):
    if p + 2 > len(d):
        raise WsaError("data ends inside a word")
    return d[p] | d[p + 1] << 8


def lcw(src, size):
    """Format 80: unpack into a fresh buffer of `size` bytes."""
    dst = bytearray()
    p = 0
    while True:
        if p >= len(src):
            raise WsaError("LCW data ends without the end command")
        cmd = src[p]
        p += 1
        if not cmd & 0x80:
            if p >= len(src):
                raise WsaError("LCW copy is truncated")
            count = (cmd & 0x70) >> 4
            start = ((cmd & 0x0F) << 8) | src[p]
            p += 1
            if start >= len(dst) and count:
                raise WsaError("LCW copy points past the data")
            dst += dst[start:start + count]
        elif cmd & 0x40 == 0:
            count = cmd & 0x3F
            if count == 0:
                break
            if p + count > len(src):
                raise WsaError("LCW literal run is truncated")
            dst += src[p:p + count]
            p += count
        elif cmd in (0xFE, 0xFF):
            raise WsaError("LCW command 0x%02x is not supported" % cmd)
        else:
            count = (cmd & 0x3F) + 3
            start = _u16(src, p)
            p += 2
            if start >= len(dst) and count:
                raise WsaError("LCW copy points past the data")
            dst += dst[start:start + count]
        if len(dst) > size:
            raise WsaError("LCW data unpacks past the delta buffer")
    return bytes(dst)


def xor_delta(pic, delta):
    """Format 40: apply an XOR delta onto the picture in place."""
    p = d = 0
    while True:
        if p >= len(delta):
            raise WsaError("XOR delta ends without the end command")
        cmd = delta[p]
        p += 1
        if cmd == 0:
            count, val = delta[p], delta[p + 1]
            p += 2
            for k in range(count):
                pic[d + k] ^= val
            d += count
        elif cmd < 0x80:
            for k in range(cmd):
                pic[d + k] ^= delta[p + k]
            p += cmd
            d += cmd
        elif cmd > 0x80:
            d += cmd & 0x7F
        else:
            word = _u16(delta, p)
            p += 2
            if word == 0:
                break
            if not word & 0x8000:
                d += word
            else:
                raise WsaError("XOR delta command 0x%04x is not supported" % word)


def parse(data):
    if len(data) < 14:
        raise WsaError("header is truncated")
    nframes, x, y, w, h, dsize, flags = (_u16(data, 2 * k) for k in range(7))
    if not nframes or not w or not h:
        raise WsaError("empty animation")
    table = 14
    if table + 4 * (nframes + 2) > len(data):
        raise WsaError("frame offset table is truncated")
    offs = [int.from_bytes(data[table + 4 * k:table + 4 * k + 4], "little") for k in range(nframes + 2)]
    has_pal = bool(flags & 1)
    loop = offs[nframes + 1] != 0
    pic = bytearray(w * h)
    frames = []
    count = nframes + 1 if loop else nframes
    for k in range(count):
        start, end = offs[k], offs[k + 1]
        if not start or end <= start or end > len(data):
            raise WsaError("frame %d lies outside the file" % k)
        xor_delta(pic, lcw(data[start:end], dsize))
        frames.append("%08x" % zlib.crc32(bytes(pic)))
    return {"frames": len(frames), "x": x, "y": y, "width": w, "height": h, "palette": has_pal, "loop": loop,
            "crc32": frames}
