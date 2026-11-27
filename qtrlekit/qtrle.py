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


def decode(pic, w, h, depth, pkt):
    if len(pkt) < 6:
        raise QtrleError("packet is too short")
    r = Reader(pkt, 4)
    header = r.byte() << 8 | r.byte()
    if header & 0x0008:
        r.p += 8
    start, lines = 0, h
    limit = w * h

    def check(ptr, n):
        pass

    row = start * w
    try:
        if depth == 1:
            ptr = row
            while lines:
                skip = r.byte()
                code = r.s8()
                if code == 0:
                    break
                if skip & 0x80:
                    lines -= 1
                    ptr = row + 16 * (skip & 0x7F)
                    row += w
                else:
                    ptr += 16 * skip
                check(ptr, 0)
                if code == -1:
                    continue
                if code < 0:
                    a, b = r.byte(), r.byte()
                    check(ptr, -code * 16)
                    for _ in range(-code):
                        for byte in (a, b):
                            for k in range(8):
                                pic[ptr] = byte >> (7 - k) & 1
                                ptr += 1
                else:
                    check(ptr, code * 16)
                    for _ in range(code * 2):
                        x = r.byte()
                        for k in range(8):
                            pic[ptr] = x >> (7 - k) & 1
                            ptr += 1
            return "decoded"
        g = GROUP[depth]
        while lines:
            lines -= 1
            ptr = row + g * r.byte()
            check(ptr, 0)
            while True:
                code = r.s8()
                if code == -1:
                    break
                if r.left() < 1:
                    raise Stop
                if code == 0:
                    ptr += g * r.byte()
                elif code < 0:
                    if depth == 16:
                        vals = [r.byte() | r.byte() << 8]
                    elif depth == 8:
                        vals = [r.byte() for _ in range(4)]
                    else:
                        vals = []
                        for _ in range(4):
                            x = r.byte()
                            vals += [x >> s & ((1 << depth) - 1) for s in range(0, 8, depth)]
                    check(ptr, -code * g)
                    for _ in range(-code):
                        for k, v in enumerate(vals):
                            pic[(ptr + k) % len(pic)] = v
                        ptr += g
                else:
                    if depth == 16:
                        check(ptr, code)
                        for _ in range(code):
                            pic[ptr] = r.byte() | r.byte() << 8
                            ptr += 1
                    elif depth == 8:
                        check(ptr, code)
                        for _ in range(code):
                            pic[ptr] = r.byte()
                            ptr += 1
                    else:
                        check(ptr, code * 4 * (g >> 2))
                        for _ in range(code * 4):
                            x = r.byte()
                            for s in range(0, 8, depth):
                                pic[ptr % len(pic)] = x >> s & ((1 << depth) - 1)
                                ptr += 1
            row += w
    except Stop:
        return "stopped"
    return "decoded"


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
