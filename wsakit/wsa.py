"""Westwood WSA animations (.wsa): frame offsets, LCW (format 80) packed XOR deltas (format 40), optional palette."""
import zlib


class WsaError(ValueError):
    pass


def _u16(d, p):
    if p + 2 > len(d):
        raise WsaError("data ends inside a word")
    return d[p] | d[p + 1] << 8


def _lcw_copy(dst, count, back, size):
    """Copy `count` bytes from `back` bytes behind the current output position.

    Source and destination may overlap; copying one byte at a time repeats the
    matched bytes the way an LZ77 back-reference does.
    """
    if count == 0:
        return
    if back < 1 or back > len(dst):
        raise WsaError("LCW copy points outside the output")
    if len(dst) + count > size:
        raise WsaError("LCW data unpacks past the delta buffer")
    start = len(dst) - back
    for k in range(count):
        dst.append(dst[start + k])


def lcw(src, size):
    """Format 80: unpack into a fresh buffer of at most `size` bytes."""
    dst = bytearray()
    p = 0
    while True:
        if p >= len(src):
            raise WsaError("LCW data ends without the end command")
        cmd = src[p]
        p += 1
        if cmd == 0x80:
            break
        if cmd < 0x80:
            # Short back-reference: 12-bit distance (one more than the gap),
            # length 3..10.
            if p >= len(src):
                raise WsaError("LCW copy is truncated")
            count = ((cmd & 0x70) >> 4) + 3
            back = (((cmd & 0x0F) << 8) | src[p]) + 1
            p += 1
            _lcw_copy(dst, count, back, size)
        elif cmd < 0xC0:
            # Literal run of 1..63 bytes.
            count = cmd & 0x3F
            if p + count > len(src):
                raise WsaError("LCW literal run is truncated")
            if len(dst) + count > size:
                raise WsaError("LCW data unpacks past the delta buffer")
            dst += src[p:p + count]
            p += count
        elif cmd < 0xFE:
            # Long back-reference: 16-bit distance, length 3..66.
            count = (cmd & 0x3F) + 3
            back = _u16(src, p)
            p += 2
            _lcw_copy(dst, count, back, size)
        elif cmd == 0xFE:
            # Fill: 16-bit count followed by the byte to repeat.
            count = _u16(src, p)
            p += 2
            if p >= len(src):
                raise WsaError("LCW fill is truncated")
            if len(dst) + count > size:
                raise WsaError("LCW data unpacks past the delta buffer")
            dst += bytes([src[p]]) * count
            p += 1
        else:
            # Very long back-reference: 16-bit count, then 16-bit distance.
            count = _u16(src, p)
            back = _u16(src, p + 2)
            p += 4
            _lcw_copy(dst, count, back, size)
    return bytes(dst)


def xor_delta(pic, delta):
    """Format 40: apply an XOR delta onto the picture in place."""
    p = d = 0

    def put(count, value):
        if d + count > len(pic):
            raise WsaError("XOR delta runs past the picture")
        for k in range(count):
            pic[d + k] ^= value

    while True:
        if p >= len(delta):
            raise WsaError("XOR delta ends without the end command")
        cmd = delta[p]
        p += 1
        if cmd == 0:
            # XOR fill: count, then the byte to XOR with.
            if p + 2 > len(delta):
                raise WsaError("XOR fill is truncated")
            count, value = delta[p], delta[p + 1]
            p += 2
            put(count, value)
            d += count
        elif cmd < 0x80:
            # XOR run: the next `cmd` bytes are XORed onto the picture.
            if p + cmd > len(delta):
                raise WsaError("XOR run is truncated")
            if d + cmd > len(pic):
                raise WsaError("XOR delta runs past the picture")
            for k in range(cmd):
                pic[d + k] ^= delta[p + k]
            p += cmd
            d += cmd
        elif cmd > 0x80:
            # Skip over `cmd & 0x7F` bytes of unchanged picture.
            d += cmd & 0x7F
        else:
            word = _u16(delta, p)
            p += 2
            if word == 0:
                break
            if not word & 0x8000:
                # Long skip over `word` unchanged bytes.
                d += word
            else:
                count = word & 0x3FFF
                if word & 0x4000:
                    # Long XOR fill: one value byte follows.
                    if p >= len(delta):
                        raise WsaError("XOR long fill is truncated")
                    put(count, delta[p])
                    p += 1
                else:
                    # Long XOR run: `count` literal bytes follow.
                    if p + count > len(delta):
                        raise WsaError("XOR long run is truncated")
                    if d + count > len(pic):
                        raise WsaError("XOR delta runs past the picture")
                    for k in range(count):
                        pic[d + k] ^= delta[p + k]
                    p += count
                d += count


def _next_offset(offs, k):
    """Start of the next frame after `k` that carries data, else None.

    The search stops before the final offset, which is the loop frame's end
    rather than the start of another frame.
    """
    for j in range(k + 1, len(offs) - 1):
        if offs[j]:
            return offs[j]
    return None


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

    def decode(start, end, what):
        if start >= len(data) or end > len(data) or end <= start:
            raise WsaError("%s lies outside the file" % what)
        xor_delta(pic, lcw(data[start:end], dsize))

    for k in range(nframes):
        start = offs[k]
        if start:
            end = _next_offset(offs, k)
            decode(start, len(data) if end is None else end, "frame %d" % k)
        # An offset of 0 means the frame stores no data: the picture stays as it was.
        frames.append("%08x" % zlib.crc32(bytes(pic)))
    out = {"frames": len(frames), "x": x, "y": y, "width": w, "height": h, "palette": has_pal, "loop": loop,
           "crc32": frames}
    if loop:
        start, end = offs[nframes], offs[nframes + 1]
        if start:
            decode(start, end, "loop frame")
        out["loop_crc32"] = "%08x" % zlib.crc32(bytes(pic))
    return out
