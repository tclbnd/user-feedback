"""Microsoft Video 1 (CRAM) frame decoder for 8-bit palettised and 16-bit RGB555 streams."""
import zlib

MAGIC = b"CRAMPAK\0"


class CramError(Exception):
    pass


class Canvas:
    """Holds the picture between frames; rows are kept top-down."""

    def __init__(self, width, height, depth):
        self.width, self.height, self.depth = width, height, depth
        self.pix = [0] * (width * height)

    def frame(self, buf):
        w, pix = self.width, self.pix
        wide, high = (self.width + 3) // 4, (self.height + 3) // 4
        counts = {"skip": 0, "fill": 0, "two": 0, "quad": 0}
        pos, skip = 0, 0
        for by in range(high):
            for bx in range(wide):
                if skip > 0:
                    skip -= 1
                    counts["skip"] += 1
                    continue
                if pos + 2 > len(buf):
                    return counts, True
                a, b = buf[pos], buf[pos + 1]
                pos += 2
                if (b & 0xFC) == 0x84:
                    skip = a - 1
                    counts["skip"] += 1
                    continue
                flags = (b << 8) | a
                if self.depth == 8:
                    if b < 0x80:
                        if pos + 2 > len(buf):
                            return counts, True
                        colors = list(buf[pos:pos + 2])
                        pos += 2
                        kind = "two"
                    elif b >= 0x90:
                        if pos + 8 > len(buf):
                            return counts, True
                        colors = list(buf[pos:pos + 8])
                        pos += 8
                        kind = "quad"
                    else:
                        colors = [a]
                        kind = "fill"
                else:
                    if b < 0x80:
                        if pos + 4 > len(buf):
                            return counts, True
                        first = buf[pos] | buf[pos + 1] << 8
                        if first & 0x8000:
                            if pos + 16 > len(buf):
                                return counts, True
                            colors = [buf[pos + 2 * i] | buf[pos + 2 * i + 1] << 8 for i in range(8)]
                            pos += 16
                            kind = "quad"
                        else:
                            colors = [first, buf[pos + 2] | buf[pos + 3] << 8]
                            pos += 4
                            kind = "two"
                    else:
                        colors = [flags]
                        kind = "fill"
                counts[kind] += 1
                # Blocks are coded bottom-up: the first block row in the
                # stream is the bottom row of the picture, and inside a
                # block the first pixel row is the block's bottom row.
                for sy in range(4):
                    y = self.height - 1 - (by * 4 + sy)
                    row = y * w + bx * 4
                    for px in range(4):
                        if kind == "fill":
                            v = colors[0]
                        elif kind == "two":
                            v = colors[flags & 1]
                        else:
                            v = colors[((px & 2) << 1) + (sy & 2) + (flags & 1)]
                        flags >>= 1
                        if 0 <= y and bx * 4 + px < w:
                            pix[row + px] = v
        return counts, False

    def crc(self):
        if self.depth == 8:
            raw = bytes(self.pix)
        else:
            raw = b"".join(v.to_bytes(2, "little") for v in self.pix)
        return "%08x" % zlib.crc32(raw)


def parse(data):
    if len(data) < 15 or data[:8] != MAGIC:
        raise CramError("not a CRAM frame pack")
    width = int.from_bytes(data[8:10], "little")
    height = int.from_bytes(data[10:12], "little")
    depth = data[12]
    if depth not in (8, 16):
        raise CramError("depth must be 8 or 16")
    count = int.from_bytes(data[13:15], "little")
    pos = 15
    frames = []
    canvas = Canvas(width, height, depth)
    for _ in range(count):
        if pos + 4 > len(data):
            raise CramError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise CramError("frame data is truncated")
        pos += 4 + n
        counts, short = canvas.frame(body)
        frames.append({"crc32": canvas.crc(), **counts, "short": short})
    return {"width": width, "height": height, "depth": depth, "frames": frames}
