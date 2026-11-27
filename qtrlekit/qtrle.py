"""QuickTime Animation (RLE) frame sequences (.qtr): decode each frame at 1, 2, 4, 8 or 16 bits per pixel."""
import zlib

MAGIC = b"QTRLESEQ"
GROUP = {1: 16, 2: 16, 4: 8, 8: 4, 16: 1}


class Stop(Exception):
    pass


class QtrleError(ValueError):
    pass


class Reader:
    def __init__(self, d, p):
        self.d, self.p = d, p

    def left(self):
        return len(self.d) - self.p

    def byte(self):
        v = self.d[self.p] if self.p < len(self.d) else 0
        self.p += 1
        return v

    def s8(self):
        v = self.byte()
        return v - 256 if v & 0x80 else v

    def peek(self):
        return self.d[self.p] if self.p < len(self.d) else 0


def _unpack(depth, x):
    if depth == 4:
        return [(x >> 4) & 0x0F, x & 0x0F]
    if depth == 2:
        return [(x >> 6) & 0x03, (x >> 4) & 0x03, (x >> 2) & 0x03, x & 0x03]
    return [(x >> 7) & 1, (x >> 6) & 1, (x >> 5) & 1, (x >> 4) & 1,
            (x >> 3) & 1, (x >> 2) & 1, (x >> 1) & 1, x & 1]


def decode(pic, w, h, depth, pkt):
    # A frame is a 4-byte chunk size, a 16-bit big-endian header, then rows.
    # A chunk that carries nothing after its size leaves the picture unchanged.
    if len(pkt) < 4:
        raise QtrleError("packet is too short")
    r = Reader(pkt, 4)
    if r.left() == 0:
        return "unchanged"
    header = r.byte() << 8 | r.byte()
    if header & 0x0008:
        for _ in range(8):
            r.byte()
    g = GROUP[depth]
    limit = w * h
    row = 0
    changed = False

    def need(n):
        if r.left() < n:
            raise Stop

    try:
        while row < h and r.left():
            need(1)
            desc = r.byte()
            if desc & 0x80:
                # High bit set: skip this many whole rows, then read the
                # within-row skip descriptor for the next drawn row.
                row += desc & 0x7F
                if row >= h:
                    break
                need(1)
                desc = r.byte()
            # The within-row skip is counted in pixel groups; scale to pixels.
            ptr = row * w + g * desc
            while r.left():
                need(1)
                code = r.s8()
                if code == -1:
                    break
                if code == 0:
                    need(1)
                    ptr += g * r.byte()
                    continue
                if code < 0:
                    count = -code
                    if depth == 16:
                        need(2)
                        value = r.byte() << 8 | r.byte()
                        if ptr + count > limit:
                            raise Stop
                        for _ in range(count):
                            pic[ptr] = value
                            ptr += 1
                    elif depth == 8:
                        need(g)
                        values = [r.byte() for _ in range(g)]
                        if ptr + count * g > limit:
                            raise Stop
                        for _ in range(count):
                            for value in values:
                                pic[ptr] = value
                                ptr += 1
                    else:
                        need(1)
                        values = _unpack(depth, r.byte())
                        # A packed run carries one pattern byte; the count is
                        # in byte-sized sub-groups of (8/depth) pixels each.
                        span = count * len(values)
                        if ptr + span > limit:
                            raise Stop
                        for _ in range(count):
                            for value in values:
                                pic[ptr] = value
                                ptr += 1
                else:
                    if depth == 16:
                        need(code * 2)
                        if ptr + code > limit:
                            raise Stop
                        for _ in range(code):
                            pic[ptr] = r.byte() << 8 | r.byte()
                            ptr += 1
                    elif depth == 8:
                        need(code)
                        if ptr + code > limit:
                            raise Stop
                        for _ in range(code):
                            pic[ptr] = r.byte()
                            ptr += 1
                    else:
                        # One literal byte yields (8/depth) pixels.
                        nbytes = code
                        need(nbytes)
                        if ptr + nbytes * (8 // depth) > limit:
                            raise Stop
                        for _ in range(nbytes):
                            for value in _unpack(depth, r.byte()):
                                pic[ptr] = value
                                ptr += 1
                changed = True
            row += 1
    except Stop:
        return "stopped"
    return "decoded" if changed else "unchanged"


def parse(data):
    if len(data) < 16 or data[:8] != MAGIC:
        raise QtrleError("not a QuickTime RLE frame sequence")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    depth = data[12]
    count = int.from_bytes(data[13:16], "little")
    if depth not in GROUP:
        raise QtrleError(f"unsupported depth {depth}")
    if not w or not h or w % GROUP[depth]:
        raise QtrleError("width must be a non-zero multiple of the depth's pixel group")
    pic = [0] * (w * h)
    pos, frames = 16, []
    for _ in range(count):
        if pos + 4 > len(data):
            raise QtrleError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise QtrleError("frame data is truncated")
        pos += 4 + n
        state = decode(pic, w, h, depth, body)
        frames.append({"result": state, "crc32": "%08x" % zlib.crc32(b"".join(v.to_bytes(2, "little") for v in pic))})
    return {"width": w, "height": h, "depth": depth, "frames": frames, "colours": len(set(pic))}
