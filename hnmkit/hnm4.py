"""Cryo HNM4 still-frame chunks (.hnc): palette chunks and intra frames, decoded through the palette."""
import zlib

MAGIC = b"HNM4SEQ\0"
PL, IZ, SD = b"PL", b"IZ", b"SD"


class HnmError(ValueError):
    pass


class Reader:
    def __init__(self, d):
        self.d, self.p = d, 0

    def byte(self):
        v = self.d[self.p] if self.p < len(self.d) else 0
        self.p += 1
        return v

    def le(self, n):
        v = 0
        for k in range(n):
            v |= self.byte() << (8 * k)
        return v

    def tell(self):
        return min(self.p, len(self.d))


class Decoder:
    def __init__(self, version, w, h):
        self.version, self.w, self.h = version, w, h
        self.cur = bytearray(w * h)
        self.pal = [0xFF000000] * 256

    def palette(self, chunk):
        eight = False
        r = Reader(chunk[8:])
        size = len(chunk) - 8
        while r.tell() < size:
            start, count = r.byte(), r.byte()
            if start == 255 and count == 255:
                break
            off = start
            while count:
                v = r.byte() << 16 | r.byte() << 8 | r.byte()
                if not eight:
                    v = ((v >> 16 & 0xFF) * 4 & 0xFF) << 16 | ((v >> 8 & 0xFF) * 4 & 0xFF) << 8 | ((v & 0xFF) * 4 & 0xFF)
                self.pal[off % 256] = v | 0xFF000000
                off += 1
                count -= 1

    def intra(self, src):
        r = Reader(src)
        size = len(src)
        n = self.w * self.h
        cur = self.cur
        state = {"buf": 0, "bits": 0}

        def bit():
            if not state["bits"]:
                state["buf"], state["bits"] = r.le(4), 32
            b = state["buf"] & 1
            state["buf"] >>= 1
            state["bits"] -= 1
            return b

        w = 0
        while r.tell() < size:
            if bit():
                if w >= n:
                    break
                cur[w] = r.byte()
                w += 1
                continue
            if bit():
                word = r.le(2)
                count, off = word & 7, -(word >> 3)
                if not count:
                    count = r.byte()
                if not count:
                    return
            else:
                count = bit() * 2
                count += bit()
                off = r.byte() - 0x100
            off += w
            if off < 0 or w + count > n:
                break
            for _ in range(count):
                cur[w] = cur[off]
                w += 1
                off += 1

    def picture(self):
        return bytes(self.cur)


def parse(data):
    if len(data) < 16 or data[:8] != MAGIC:
        raise HnmError("not an HNM4 chunk sequence")
    version = data[8]
    w = int.from_bytes(data[10:12], "little")
    h = int.from_bytes(data[12:14], "little")
    count = int.from_bytes(data[14:16], "little")
    if not w or not h or h % 2:
        raise HnmError("width must be non-zero and height even")
    dec = Decoder(version, w, h)
    pos, frames, palettes, skipped = 16, [], 0, 0
    for _ in range(count):
        if pos + 4 > len(data):
            raise HnmError("chunk table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        ch = data[pos + 4:pos + 4 + n]
        if len(ch) < n:
            raise HnmError("chunk is truncated")
        pos += 4 + n
        if len(ch) < 8:
            skipped += 1
            continue
        cid = ch[4:6]
        if cid == PL:
            dec.palette(ch)
            palettes += 1
        elif cid == IZ:
            if len(ch) < 12:
                skipped += 1
                continue
            dec.cur = bytearray(w * h)
            dec.intra(ch[12:])
            pic = dec.picture()
            rgb = b"".join(bytes([v >> 16 & 0xFF, v >> 8 & 0xFF, v & 0xFF]) for v in (dec.pal[i] for i in pic))
            frames.append({"crc32": "%08x" % zlib.crc32(rgb), "colours": len(set(pic))})
        else:
            skipped += 1
    return {"version": version, "width": w, "height": h, "palettes": palettes, "frames": frames, "skipped": skipped}
