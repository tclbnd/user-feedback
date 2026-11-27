"""RL2 video decoder (run-length frames drawn over an optional background frame)."""
import zlib

MAGIC = b"RL2PAK\0\0"


class Rl2Error(Exception):
    pass


def rle(w, h, data, out, base, back):
    """Draw one RLE frame into out (a bytearray of w*h). back is the background frame or None."""
    size = w * h
    o = 0
    end = len(data)
    p = 0
    while p < end:
        val = data[p]
        p += 1
        n = 1
        if val >= 0x80:
            if p >= end:
                break
            n = data[p] - 1
            p += 1
        if o >= size:
            break
        val &= 0x7F
        while n > 0 and o < size:
            out[o] = val
            o += 1
            n -= 1


def parse(data):
    if len(data) < 8 + 6 + 768 + 4 or data[:8] != MAGIC:
        raise Rl2Error("not an RL2 frame pack")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    base = int.from_bytes(data[12:14], "little")
    if w == 0 or h == 0:
        raise Rl2Error("empty picture")
    if base >= w * h:
        raise Rl2Error("video base is outside the picture")
    pal6 = data[14:14 + 768]
    pal = bytes(pal6)
    pos = 14 + 768
    bsize = int.from_bytes(data[pos:pos + 4], "little")
    pos += 4
    bdata = data[pos:pos + bsize]
    if len(bdata) < bsize:
        raise Rl2Error("background frame is truncated")
    pos += bsize
    if bsize:
        raise Rl2Error("background frames are not supported")
    back = None
    if pos + 2 > len(data):
        raise Rl2Error("frame count is missing")
    count = int.from_bytes(data[pos:pos + 2], "little")
    pos += 2
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise Rl2Error("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise Rl2Error("frame data is truncated")
        pos += 4 + n
        pic = bytearray(w * h)
        rle(w, h, body, pic, base, back)
        rgb = b"".join(pal[3 * v:3 * v + 3] for v in pic)
        frames.append({"crc32": "%08x" % zlib.crc32(rgb), "first_row": "%08x" % zlib.crc32(rgb[:3 * w]),
                       "last_row": "%08x" % zlib.crc32(rgb[-3 * w:])})
    return {"width": w, "height": h, "video_base": base, "background": back is not None, "frames": frames}
