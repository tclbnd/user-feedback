"""Duck DK3 ADPCM sound (.dk3): stereo sum/difference IMA ADPCM blocks."""
import zlib

STEP = (7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97,
        107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
        876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428,
        4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350,
        22385, 24623, 27086, 29794, 32767)
INDEX = (-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)


class Dk3Error(ValueError):
    pass


def _s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def _clip(v):
    return max(-32768, min(32767, v))


class Channel:
    def __init__(self, pred, index):
        self.pred, self.index = pred, index

    def expand(self, nib):
        step = STEP[self.index]
        diff = ((2 * (nib & 7) + 1) * step) >> 3
        p = self.pred - diff if nib & 8 else self.pred + diff
        self.pred = max(-32768, min(32767, p))
        self.index = max(0, min(88, self.index + INDEX[nib]))


def block(buf):
    """Decode one block; returns the left and right samples."""
    frames = (len(buf) - 16) * 2 // 3
    if frames <= 0:
        return [], []
    s = Channel(_s16(buf[0] | buf[1] << 8), buf[4])
    d = Channel(_s16(buf[2] | buf[3] << 8), buf[5])
    s.index, d.index = min(s.index, 88), min(d.index, 88)
    pos, top, last = 16, False, 0

    def nib():
        nonlocal pos, top, last
        if top:
            top = False
            return last & 0x0F
        last = buf[pos] if pos < len(buf) else 0
        pos += 1
        top = True
        return last >> 4

    left, right = [], []
    while len(left) < frames:
        s.expand(nib())
        d.expand(nib())
        left.append(_clip(s.pred - d.pred))
        right.append(_clip(s.pred + d.pred))
        s.expand(nib())
        left.append(_clip(s.pred - d.pred))
        right.append(_clip(s.pred + d.pred))
    return left, right


def parse(data):
    if len(data) < 16 or data[:8] != b"DK3SOUND":
        raise Dk3Error("not a DK3 sound file")
    rate = int.from_bytes(data[8:12], "little")
    channels = data[12] | data[13] << 8
    align = data[14] | data[15] << 8
    if channels != 2:
        raise Dk3Error("DK3 sound is always stereo")
    if align < 16:
        raise Dk3Error("block size is too small")
    body = data[16:]
    left, right, blocks = [], [], 0
    for k in range(0, len(body), align):
        l, r = block(body[k:k + align])
        if l:
            blocks += 1
        left += l
        right += r
    chans = []
    for v in (left, right):
        raw = b"".join(x.to_bytes(2, "little", signed=True) for x in v)
        chans.append({"samples": len(v), "peak": max((abs(x) for x in v), default=0), "sum": sum(v),
                      "crc32": "%08x" % zlib.crc32(raw)})
    return {"rate": rate, "channels": 2, "block_size": align, "blocks": blocks, "frames": len(left),
            "duration": round(len(left) / rate, 4) if rate else None, "left": chans[0], "right": chans[1]}
