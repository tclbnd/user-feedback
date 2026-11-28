"""Macintosh HCOM sound (.hcom): Huffman-coded 8-bit samples with optional delta coding."""
import zlib


class HcomError(ValueError):
    pass


def s16(b, p):
    v = b[p] << 8 | b[p + 1]
    return v - 0x10000 if v & 0x8000 else v


class Decoder:
    def __init__(self, extra):
        if len(extra) <= 7:
            raise HcomError("dictionary header is too short")
        n = extra[0] << 8 | extra[1]
        if n == 0 or len(extra) < n * 4 + 7:
            raise HcomError("dictionary is truncated")
        self.delta = int.from_bytes(extra[2:6], "big")
        self.sample = 128
        self.first = extra[-1]
        self.dict = []
        for i in range(n):
            l, r = s16(extra, 6 + 4 * i), s16(extra, 8 + 4 * i)
            self.dict.append((l, r))
        self.node = 0

    def chunk(self, data):
        out = bytearray()
        self.node = 0
        for byte in data:
            for k in range(8):
                bit = byte >> k & 1
                self.node = self.dict[self.node][1] if bit else self.dict[self.node][0]
                l, r = self.dict[self.node]
                if l == -1:
                    self.sample = (self.sample + r) & 0xFF
                    out.append(self.sample)
                    self.node = 0
        return bytes(out)


def parse(data):
    if len(data) < 12 or data[:8] != b"HCOMPACK":
        raise HcomError("not an HCOM pack")
    rate = data[8] | data[9] << 8
    elen = data[10] | data[11] << 8
    extra = data[12:12 + elen]
    dec = Decoder(extra)
    pos = 12 + elen
    count = data[pos] | data[pos + 1] << 8 if pos + 2 <= len(data) else 0
    pos += 2
    samples, chunks = bytearray(), []
    for _ in range(count):
        if pos + 4 > len(data):
            raise HcomError("chunk table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise HcomError("chunk is truncated")
        pos += 4 + n
        s = dec.chunk(body)
        chunks.append(len(s))
        samples += s
    signed = [v - 128 for v in samples]
    return {"rate": rate, "delta": bool(dec.delta), "entries": len(dec.dict), "chunks": chunks, "samples": len(samples),
            "peak": max((abs(v) for v in signed), default=0), "sum": sum(signed), "crc32": "%08x" % zlib.crc32(bytes(samples))}
