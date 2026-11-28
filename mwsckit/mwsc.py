"""MatchWare screen capture (MWSC) frames: zlib-compressed runs of BGR pixels.

A run record is a 24-bit little-endian BGR pixel followed by one run byte:

* run 1..254 -- fill that many pixels with the pixel;
* run 0      -- long fill: the run byte is followed by a 32-bit little-endian
                pixel count (the run byte itself cannot express more than 254);
* run 255    -- copy: the three pixel bytes hold a 24-bit little-endian count
                of pixels copied unchanged from the previous frame.

Pixels are laid out from the bottom picture row upwards, each row running
left to right. The internal picture buffer therefore keeps rows bottom-first;
callers that want a top-to-bottom RGB picture reverse and re-order on output.
"""
import struct
import zlib


class MwscError(ValueError):
    pass


# Run byte tags.
RUN_LONG = 0    # extended fill, u32 count follows the record
RUN_COPY = 255  # copy u24 pixels from the previous frame


class Mwsc:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.stride = w * 3
        self.size = self.stride * h
        # Previous picture in native (bottom-up BGR) order. A blank frame
        # stands in until the first keyframe arrives.
        self.prev = bytearray(self.size)

    def frame(self, pkt):
        d = zlib.decompressobj()
        try:
            data = d.decompress(pkt)
        except zlib.error:
            raise MwscError("inflate failed")

        cur = bytearray(self.size)
        prev = self.prev
        p = 0          # byte offset into the linear (bottom-up) picture
        intra = True
        i = 0
        n = len(data)

        while i < n:
            if i + 4 > n:
                raise MwscError("truncated run record")
            b, g, r = data[i], data[i + 1], data[i + 2]
            run = data[i + 3]
            i += 4

            if run == RUN_COPY:
                # The pixel bytes carry the copy count (u24 little-endian).
                count = b | (g << 8) | (r << 16)
                intra = False
                end = p + count * 3
                if end > self.size:
                    raise MwscError("copy run overruns picture")
                cur[p:end] = prev[p:end]
                p = end
                continue

            if run == RUN_LONG:
                if i + 4 > n:
                    raise MwscError("truncated long run count")
                count = struct.unpack_from("<I", data, i)[0]
                i += 4
            else:
                count = run

            end = p + count * 3
            if end > self.size:
                raise MwscError("fill run overruns picture")
            cur[p:end] = bytes((b, g, r)) * count
            p = end

        if p != self.size:
            raise MwscError("frame does not cover the whole picture")

        self.prev = cur
        return cur, intra


def _to_rgb_top_down(buf, w, h, stride):
    """Turn a native bottom-up BGR buffer into top-to-bottom RGB bytes."""
    img = bytearray()
    for row in range(h - 1, -1, -1):
        line = buf[row * stride:(row + 1) * stride]
        rgb = bytearray(line)
        rgb[0::3] = line[2::3]  # B <- R
        rgb[2::3] = line[0::3]  # R <- B
        img.extend(rgb)
    return bytes(img)


def parse(data):
    if data[:8] != b"MWSCPACK":
        raise MwscError("not an MWSC pack")
    w, h, count = struct.unpack_from("<HHH", data, 8)
    if not w or not h:
        raise MwscError("empty picture")
    v = Mwsc(w, h)
    pos = 14
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise MwscError("frame table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        pkt = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(pkt) < n:
            raise MwscError("frame is truncated")
        try:
            cur, intra = v.frame(pkt)
        except MwscError:
            frames.append({"state": "error", "crc32": None})
            continue
        img = _to_rgb_top_down(cur, w, h, v.stride)
        frames.append({"state": "decoded", "key": intra,
                       "crc32": "%08x" % zlib.crc32(img)})
    return {"width": w, "height": h, "frames": frames}
