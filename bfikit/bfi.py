"""Interplay BFI movies (.bfi): palette and the chain-coded 8-bit frames."""
import zlib


class BfiError(ValueError):
    pass


def palette(raw):
    return [tuple((raw[3 * k + c] << 2) & 0xFF for c in range(3)) for k in range(len(raw) // 3)]


def decode(pic, pkt):
    """Apply one frame to the persistent picture; returns 'decoded', 'stopped' or raises."""
    end = len(pic)
    p = 4
    d = 0
    lentab = (0, 2, 0, 1)

    def byte():
        nonlocal p
        v = pkt[p] if p < len(pkt) else 0
        p += 1
        return v

    def le16():
        return byte() | byte() << 8

    while d != end:
        b = byte()
        code, length = b >> 6, b & 0x3F
        offset = 0
        if length == 0:
            if code == 1:
                offset = le16()
                length = byte()
            else:
                length = le16()
        elif code == 1:
            offset = byte()
        if d + (length << lentab[code]) > end:
            raise BfiError("chain runs past the picture")
        if code == 0:
            if length > len(pkt) - p:
                raise BfiError("normal chain runs past the frame data")
            pic[d:d + length] = pkt[p:p + length]
            p += length
            d += length
        elif code == 1:
            src = d - offset
            n = length
            if src < 0:
                raise BfiError("back chain points before the picture")
            for k in range(n):
                pic[d + k] = pic[src + k]
            d += n
        elif code == 2:
            pic[d:d + length] = bytes(length)
            d += length
        else:
            c1, c2 = byte(), byte()
            for k in range(length):
                pic[d + 2 * k] = c1
                pic[d + 2 * k + 1] = c1
            d += 2 * length
    return "decoded"


def parse(data):
    if len(data) < 16 or data[:8] != b"BFIMOVIE":
        raise BfiError("not a BFI movie")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    ncol = int.from_bytes(data[12:14], "little")
    count = int.from_bytes(data[14:16], "little")
    if not w or not h:
        raise BfiError("empty picture")
    if ncol > 256:
        raise BfiError("palette is too large")
    pos = 16
    if pos + 3 * ncol > len(data):
        raise BfiError("palette is truncated")
    pal = palette(data[pos:pos + 3 * ncol]) + [(0, 0, 0)] * (256 - ncol)
    pos += 3 * ncol
    pic = bytearray(w * h)
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise BfiError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise BfiError("frame data is truncated")
        pos += 4 + n
        try:
            state = decode(pic, body)
        except BfiError:
            state = "error"
        rgb = b"".join(bytes(pal[v]) for v in pic)
        frames.append({"state": state, "crc32": "%08x" % zlib.crc32(rgb)})
    return {"width": w, "height": h, "colours": ncol, "frames": frames}
