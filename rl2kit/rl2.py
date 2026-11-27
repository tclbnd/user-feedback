"""RL2 video decoder (run-length frames drawn over an optional background frame)."""
import zlib

MAGIC = b"RL2PAK\0\0"


class Rl2Error(Exception):
    pass


def draw_rle(data, canvas, start, what):
    """Draw one RL2 run-length coded stream into canvas, beginning at byte `start`.

    Stream coding:
      byte < 0x80              -> one literal pixel carrying that palette index
      byte >= 0x80, count n    -> n pixels of palette index (byte & 0x7f);
                                  a zero count writes nothing (end-of-stream marker)

    Only the pixels the stream reaches are touched. Everything before `start`
    and every pixel the stream does not reach is left intact, so partial frames
    let the background frame (and pixels drawn by earlier frames) show through.
    Returns the index just past the last pixel written.
    """
    size = len(canvas)
    o = start
    p = 0
    end = len(data)
    while p < end:
        code = data[p]
        p += 1
        if code < 0x80:
            if o >= size:
                raise Rl2Error(what + " runs past the picture")
            canvas[o] = code
            o += 1
            continue
        if p >= end:
            raise Rl2Error(what + " has a truncated run")
        count = data[p]
        p += 1
        color = code & 0x7F
        if o + count > size:
            raise Rl2Error(what + " runs past the picture")
        canvas[o:o + count] = bytes((color,)) * count
        o += count
    return o


def expand_palette(pal6):
    """Expand the 256 VGA palette entries (6 bits per channel) to 8-bit RGB.

    The DAC stores each channel in six bits, so used verbatim the colours only
    reach about a quarter of full brightness and look black. The hardware shows
    them by replicating the top bits into the low bits, spanning the full range.
    """
    lut = []
    for i in range(256):
        r, g, b = pal6[3 * i:3 * i + 3]
        lut.append(bytes(((c << 2) | (c >> 4)) for c in (r, g, b)))
    return lut


def parse(data):
    if len(data) < 8 + 6 + 768 + 4 or data[:8] != MAGIC:
        raise Rl2Error("not an RL2 frame pack")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    base = int.from_bytes(data[12:14], "little")
    if w == 0 or h == 0:
        raise Rl2Error("empty picture")
    size = w * h
    if base >= size:
        raise Rl2Error("video base is outside the picture")
    pal6 = data[14:14 + 768]
    lut = expand_palette(pal6)
    pos = 14 + 768
    bsize = int.from_bytes(data[pos:pos + 4], "little")
    pos += 4
    bdata = data[pos:pos + bsize]
    if len(bdata) < bsize:
        raise Rl2Error("background frame is truncated")
    pos += bsize
    if pos + 2 > len(data):
        raise Rl2Error("frame count is missing")
    count = int.from_bytes(data[pos:pos + 2], "little")
    pos += 2

    # One persistent picture: the background frame paints the whole canvas once,
    # then every frame is drawn over it at the video base as an incremental delta.
    canvas = bytearray(size)
    has_background = bsize != 0
    if has_background:
        if draw_rle(bdata, canvas, 0, "background frame") != size:
            raise Rl2Error("background frame does not fill the picture")

    frames = []
    for idx in range(count):
        if pos + 4 > len(data):
            raise Rl2Error("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        pos += 4
        body = data[pos:pos + n]
        if len(body) < n:
            raise Rl2Error("frame data is truncated")
        pos += n
        draw_rle(body, canvas, base, "frame %d" % idx)
        rgb = b"".join(lut[v] for v in canvas)
        frames.append({"crc32": "%08x" % zlib.crc32(rgb),
                       "first_row": "%08x" % zlib.crc32(rgb[:3 * w]),
                       "last_row": "%08x" % zlib.crc32(rgb[-3 * w:])})
    return {"width": w, "height": h, "video_base": base,
            "background": has_background, "frames": frames}
