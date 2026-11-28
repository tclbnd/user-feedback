"""Creature Shock AVS video (.avs): palette blocks and vector-quantised 318 x 198 frames."""
import zlib

W, H = 318, 198
SIZES = {0: (3, 3), 1: (3, 3), 2: (2, 2), 3: (3, 2)}


class AvsError(ValueError):
    pass


class Decoder:
    def __init__(self):
        self.pic = bytearray(W * H)
        self.pal = [(0, 0, 0)] * 256

    def frame(self, buf):
        if len(buf) < 4:
            raise AvsError("block header is truncated")
        sub, typ = buf[0], buf[1]
        p = 4
        if typ == 3:
            if p + 4 > len(buf):
                raise AvsError("palette header is truncated")
            first = buf[p] | buf[p + 1] << 8
            last = min(256, buf[p + 2] | buf[p + 3] << 8)
            if len(buf) - p < 4 + 3 * max(0, last - first):
                raise AvsError("palette block is damaged")
            p += 4
            for i in range(first, last):
                self.pal[i] = tuple((c << 2) & 0xFF for c in buf[p:p + 3])
                p += 3
            typ = 1
        if typ != 1:
            raise AvsError("not a video block")
        if sub not in SIZES:
            raise AvsError("unknown frame type %d" % sub)
        vw, vh = SIZES[sub]
        n = vw * vh
        if len(buf) - p < 256 * n:
            raise AvsError("vector table is truncated")
        vec = buf[p:p + 256 * n]
        t = p + 256 * n
        bits = None
        if sub != 0:
            row_bytes = (318 // vw + 7) // 8
            size = row_bytes * (198 // vh)
            if len(buf) - t < size:
                raise AvsError("change map is truncated")
            bits = buf[t:t + size]
            t += size
        bpos = 0
        for y in range(0, H, vh):
            for x in range(0, W, vw):
                take = True
                if bits is not None:
                    take = bits[bpos >> 3] >> (bpos & 7) & 1
                    bpos += 1
                if take:
                    if t >= len(buf):
                        raise AvsError("vector indices are truncated")
                    v = vec[buf[t] * n:(buf[t] + 1) * n]
                    t += 1
                    for j in range(vw):
                        for r in range(vh):
                            self.pic[(y + r) * W + x + j] = v[r * vw + j]
        return sub == 0


def parse(data):
    if len(data) < 10 or data[:8] != b"AVSMOVIE":
        raise AvsError("not an AVS movie")
    count = data[8] | data[9] << 8
    pos = 10
    dec = Decoder()
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise AvsError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise AvsError("frame data is truncated")
        pos += 4 + n
        try:
            key = dec.frame(body)
            ok = True
        except (AvsError, IndexError):
            key, ok = False, False
        rgb = b"".join(bytes(dec.pal[v]) for v in dec.pic)
        frames.append({"ok": ok, "key": key, "crc32": "%08x" % zlib.crc32(rgb)})
    return {"width": W, "height": H, "frames": frames}
