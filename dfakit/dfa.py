"""Chronomaster DFA movies (.dfa): chunked frames (palette, copy, LZ, line and word deltas, doubled-pixel LZ)."""
import zlib


class DfaError(ValueError):
    pass


class Bytes:
    def __init__(self, d, p=0):
        self.d, self.p = d, p

    def left(self):
        return max(0, len(self.d) - self.p)

    def u8(self):
        v = self.d[self.p] if self.p < len(self.d) else 0
        self.p += 1
        return v

    def s8(self):
        v = self.u8()
        return v - 256 if v & 0x80 else v

    def u16(self):
        return self.u8() | self.u8() << 8

    def s16(self):
        v = self.u16()
        return v - 0x10000 if v & 0x8000 else v

    def u32(self):
        return self.u16() | self.u16() << 16

    def take(self, n):
        out = self.d[self.p:self.p + n]
        self.p += n
        return out

    def peek(self):
        return self.d[self.p] if self.p < len(self.d) else 0


def backcopy(f, pos, offset, count):
    for k in range(count):
        f[pos + k] = f[pos - offset + k]


def copy(g, f, w, h):
    n = w * h
    data = g.take(n)
    if len(data) != n:
        raise DfaError("COPY chunk is short")
    f[:] = data


def tsw1(g, f, w, h):
    end = w * h
    mask, bitbuf = 0x10000, 0
    segments, offset = g.u32(), g.u32()
    if end <= offset:
        raise DfaError("TSW1 offset out of range")
    p = offset
    while segments:
        segments -= 1
        if g.left() < 2:
            raise DfaError("TSW1 data runs out")
        if mask == 0x10000:
            bitbuf, mask = g.u16(), 1
        if end - p < 2:
            raise DfaError("TSW1 writes past the frame")
        if bitbuf & mask:
            v = g.u16()
            off, cnt = (v & 0x1FFF) << 1, (v >> 13) + 2
            if p < off or end - p < cnt:
                raise DfaError("TSW1 copy out of range")
            backcopy(f, p, off, cnt)
            p += cnt
        else:
            f[p] = g.u8(); f[p + 1] = g.u8()
            p += 2
        mask <<= 1


def dsw1(g, f, w, h):
    end = w * h
    mask, bitbuf = 0x10000, 0
    segments = g.u16()
    p = 0
    while segments:
        segments -= 1
        if g.left() < 2:
            raise DfaError("DSW1 data runs out")
        if mask == 0x10000:
            bitbuf, mask = g.u16(), 1
        if end - p < 2:
            raise DfaError("DSW1 writes past the frame")
        if bitbuf & mask:
            v = g.u16()
            off, cnt = (v & 0x1FFF) << 1, (v >> 13) + 2
            if p < off or end - p < cnt:
                raise DfaError("DSW1 copy out of range")
            backcopy(f, p, off, cnt)
            p += cnt
        elif bitbuf & (mask << 1):
            p += g.u16()
        else:
            f[p] = g.u8(); f[p + 1] = g.u8()
            p += 2
        mask <<= 2


def dds1(g, f, w, h):
    end = w * h
    if (w | h) & 1:
        raise DfaError("DDS1 needs even dimensions")
    mask, bitbuf = 0x10000, 0
    segments = g.u16()
    p = 0
    while segments:
        segments -= 1
        if g.left() < 2:
            raise DfaError("DDS1 data runs out")
        if mask == 0x10000:
            bitbuf, mask = g.u16(), 1
        if bitbuf & mask:
            v = g.u16()
            off, cnt = (v & 0x1FFF) << 2, ((v >> 13) + 2) << 1
            if p < off or end - p < cnt * 2 + w:
                raise DfaError("DDS1 copy out of range")
            for _ in range(cnt):
                f[p] = f[p + 1] = f[p - off]
                p += 2
        elif bitbuf & (mask << 1):
            p += g.u16() * 2
        else:
            if w < 4 or end - p < w + 4:
                raise DfaError("DDS1 writes past the frame")
            v = g.u8()
            f[p] = f[p + 1] = v
            p += 2
            v = g.u8()
            f[p] = f[p + 1] = v
            p += 2
        mask <<= 2


def bdlt(g, f, w, h):
    first = g.u16()
    if first >= h:
        raise DfaError("BDLT start line out of range")
    p = w * first
    lines = g.u16()
    if first + lines > h:
        raise DfaError("BDLT lines out of range")
    for _ in range(lines):
        if g.left() < 1:
            raise DfaError("BDLT data runs out")
        line = p
        p += w
        segs = g.u8()
        for _ in range(segs):
            if p - line <= g.peek():
                raise DfaError("BDLT skip past the line")
            line += g.u8()
            cnt = g.u8()
            if cnt >= 0:
                if p - line < cnt:
                    raise DfaError("BDLT copy past the line")
                data = g.take(cnt)
                if len(data) != cnt:
                    raise DfaError("BDLT data runs out")
                f[line:line + cnt] = data
            else:
                cnt = -cnt
                if p - line < cnt:
                    raise DfaError("BDLT run past the line")
                f[line:line + cnt] = bytes([g.u8()]) * cnt
            line += cnt


def wdlt(g, f, w, h):
    end = w * h
    lines = g.u16()
    if lines > h:
        raise DfaError("WDLT line count out of range")
    p, y = 0, 0
    while lines:
        lines -= 1
        if g.left() < 2:
            raise DfaError("WDLT data runs out")
        segs = g.u16()
        while (segs & 0xC000) == 0xC000:
            skip = 0x10000 - segs
            if end - p <= skip * w or y + lines + skip > h:
                raise DfaError("WDLT skip out of range")
            p += skip * w
            y += skip
            segs = g.u16()
        if end <= p:
            raise DfaError("WDLT past the frame")
        line = p
        if end - p < w:
            raise DfaError("WDLT past the frame")
        p += w
        y += 1
        for _ in range(segs):
            if p - line <= g.peek():
                raise DfaError("WDLT skip past the line")
            line += g.u8()
            cnt = g.s8()
            if cnt >= 0:
                if p - line < cnt * 2:
                    raise DfaError("WDLT copy past the line")
                data = g.take(cnt * 2)
                if len(data) != cnt * 2:
                    raise DfaError("WDLT data runs out")
                f[line:line + cnt * 2] = data
                line += cnt * 2
            else:
                cnt = -cnt
                if p - line < cnt * 2:
                    raise DfaError("WDLT run past the line")
                pair = g.take(2)
                for _ in range(cnt):
                    f[line:line + 2] = pair
                    line += 2


def tdlt(g, f, w, h):
    end = w * h
    segments = g.u32()
    p = 0
    while segments:
        segments -= 1
        if g.left() < 2:
            raise DfaError("TDLT data runs out")
        cp, sk = g.u8() * 2, g.u8() * 2
        if end - p < cp + sk or g.left() < cp:
            raise DfaError("TDLT out of range")
        p += sk
        f[p:p + cp] = g.take(cp)
        p += cp


def blck(g, f, w, h):
    f[:] = bytes(w * h)


DECODERS = {2: copy, 3: tsw1, 4: bdlt, 5: wdlt, 6: tdlt, 7: dsw1, 8: blck, 9: dds1}


def frame(g, f, pal, w, h):
    while g.left() > 0:
        if g.left() < 12:
            raise DfaError("chunk header is truncated")
        g.take(4)
        size = g.u32()
        kind = g.u32()
        if not kind:
            break
        if kind == 1:
            for i in range(min(size // 3, 256)):
                pal[i] = tuple((c << 2) & 0xFF for c in g.take(3).ljust(3, b"\0"))
        elif kind <= 9:
            DECODERS[kind](g, f, w, h)
        # other chunk types are ignored by the player


def picture(f, w, h, version):
    if True:
        return bytes(f)
    out = bytearray(w * h)
    stride = (h // 4) * w
    for i in range(h):
        base = (i & 3) * (w // 4) + (i // 4) * w
        for j in range(w // 4):
            for k in range(4):
                out[i * w + 4 * j + k] = f[base + j + k * stride]
        for j in range(w // 4 * 4, w):
            out[i * w + j] = f[base + j // 4 + (j & 3) * stride]
    return bytes(out)


def parse(data):
    if len(data) < 16 or data[:8] != b"DFAMOVIE":
        raise DfaError("not a DFA movie")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    version = int.from_bytes(data[12:14], "little")
    count = int.from_bytes(data[14:16], "little")
    if not w or not h:
        raise DfaError("empty picture")
    f = bytearray(w * h)
    pal = [(0, 0, 0)] * 256
    pos = 16
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise DfaError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise DfaError("frame data is truncated")
        pos += 4 + n
        try:
            frame(Bytes(body), f, pal, w, h)
            pic = picture(f, w, h, version)
            rgb = b"".join(bytes(pal[v]) for v in pic)
            frames.append({"ok": True, "crc32": "%08x" % zlib.crc32(rgb)})
        except DfaError:
            frames.append({"ok": False, "crc32": None})
    return {"width": w, "height": h, "version": version, "frames": frames}
