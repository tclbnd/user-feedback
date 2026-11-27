"""DXA video frame sequences (.dxs): decode each frame to 8-bit palette indices."""
import zlib

MAGIC = b"DXASEQ\0\0"

# Full-frame compression modes: one palette index per pixel, w*h bytes.
# 2/4 stand on their own (key frames); 3/5 are deltas over the previous frame.
KEY_FULL = (2, 4)
DELTA_FULL = (3, 5)
# Block-based modes added by ScummVM: a 12-byte big-endian header, an RLE table
# of per-block type codes, then the block payloads.
BLOCK_KEY = 12
BLOCK_DELTA = 13

BLOCK = 4  # blocks are 4x4 pixels


class DxaError(ValueError):
    pass


def _rle_block_types(region, count):
    """Expand the block-type table.

    A byte below 32 is a literal block type; a byte >= 32 is a run marker that
    is followed by a type byte and repeats it marker-31 times. Only the first
    count block types are meaningful, so a final run is clipped to fit.
    """
    out = []
    p = 0
    while len(out) < count:
        if p >= len(region):
            raise DxaError("block type table is truncated")
        b = region[p]
        p += 1
        if b >= 32:
            if p >= len(region):
                raise DxaError("block type run is truncated")
            run = b - 31
            value = region[p]
            p += 1
            out.extend([value] * min(run, count - len(out)))
        else:
            out.append(b)
    return out


class Decoder:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.n = w * h
        # Reference picture; black until a frame has been decoded.
        self.prev = bytes(self.n)
        # Palette kept as raw RGB triples in stored order (R, G, B).
        self.pal = [(0, 0, 0)] * 256

    # -- full-frame modes ---------------------------------------------------
    def _full_frame(self, data, key):
        if len(data) < self.n:
            raise DxaError("frame data is shorter than the picture")
        src = data[:self.n]
        if key:
            return bytearray(src)
        # Delta: a zero index leaves the reference pixel untouched.
        pic = bytearray(self.prev)
        for i, v in enumerate(src):
            if v:
                pic[i] = v
        return pic

    # -- block modes --------------------------------------------------------
    def _block_frame(self, raw, key):
        if len(raw) < 12 + 32:
            raise DxaError("block frame header is truncated")
        cols = self.w // BLOCK
        rows = self.h // BLOCK
        nblocks = cols * rows

        types = _rle_block_types(raw[12:12 + 32], nblocks)
        payload = raw[12 + 32:]

        pic = bytearray(self.n) if key else bytearray(self.prev)

        # Most block frames are one flat stream. A keyframe whose block data is
        # split into two half-grid streams carries one separator byte between
        # them; detect that by the payload being exactly one byte longer than
        # the block sizes account for.
        expected = sum(self._block_size(t) for t in types)
        two_stream = len(payload) == expected + 1
        if not two_stream and len(payload) != expected:
            raise DxaError("block payload has an unexpected length")
        half = nblocks // 2

        pos = 0
        for bi, t in enumerate(types):
            if two_stream and bi == half:
                pos += 1  # skip the inter-stream separator byte
            by, bx = divmod(bi, cols)
            oy, ox = by * BLOCK, bx * BLOCK

            if t == 0:
                # Skip: keep the pixel already in the picture (black on a
                # key frame, the reference pixel on a delta).
                continue
            if t == 1:
                pos, px = self._take(payload, pos, 16)
                self._put_block(pic, oy, ox, px)
            elif t == 2:
                # Two colours and one 1-bit row mask per block row.
                pos, data = self._take(payload, pos, 6)
                c0, c1 = data[0], data[1]
                masks = data[2:6]
                px = bytearray(16)
                for ry in range(4):
                    m = masks[ry]
                    for rx in range(4):
                        px[ry * 4 + rx] = c1 if (m >> (3 - rx)) & 1 else c0
                self._put_block(pic, oy, ox, px)
            elif t == 3 or t == 8:
                # Solid fill with one palette index.
                pos, data = self._take(payload, pos, 1)
                self._put_block(pic, oy, ox, bytes([data[0]]) * 16)
            elif t == 4:
                # Three colours plus one index byte per pixel; index 3 is
                # transparent and keeps the pixel already in the picture.
                pos, data = self._take(payload, pos, 19)
                colors = data[:3]
                idx = data[3:19]
                px = bytearray(16)
                for i in range(16):
                    code = idx[i] & 3
                    if code == 3:
                        ry, rx = divmod(i, 4)
                        px[i] = pic[(oy + ry) * self.w + ox + rx]
                    else:
                        px[i] = colors[code]
                self._put_block(pic, oy, ox, px)
            else:
                pos, px = self._pattern_block(t, payload, pos)
                self._put_block(pic, oy, ox, px)
        if pos != len(payload):
            raise DxaError("block payload has trailing bytes")
        return pic

    def _block_size(self, t):
        """Payload byte count of a block of the given type."""
        if t == 0:
            return 0
        if t == 1:
            return 16
        if t == 2:
            return 6
        if t == 3 or t == 8:
            return 1
        if t == 4:
            return 19
        if t == 5:
            return 1
        if t == 33:
            return 5
        if 10 <= t <= 15:
            return t - 9
        raise DxaError("unknown block type %d" % t)

    def _take(self, payload, pos, k):
        if pos + k > len(payload):
            raise DxaError("block payload is truncated")
        return pos + k, payload[pos:pos + k]

    def _pattern_block(self, t, payload, pos):
        """Keyframe pattern blocks.

        The block stores a short list of palette indices which are cycled over
        the 16 pixels in order. Types 10..15 hold t-9 indices, type 33 holds
        five, and type 5 is a one-index solid block.
        """
        if t == 5:
            ncolors = 1
        elif t == 33:
            ncolors = 5
        elif 10 <= t <= 15:
            ncolors = t - 9
        else:
            raise DxaError("unknown block type %d" % t)
        pos, colors = self._take(payload, pos, ncolors)
        px = bytes(colors[i % ncolors] for i in range(16))
        return pos, px

    def _put_block(self, pic, oy, ox, px):
        for ry in range(BLOCK):
            row = (oy + ry) * self.w + ox
            pic[row:row + BLOCK] = px[ry * BLOCK:(ry + 1) * BLOCK]

    def frame(self, pkt):
        p = 0
        if pkt[0:4] == b"CMAP":
            p = 4
            for k in range(256):
                # Stored R, G, B: keep the channels in the order they are read.
                self.pal[k] = (pkt[p], pkt[p + 1], pkt[p + 2])
                p += 3
        tag = pkt[p:p + 4]
        p += 4
        if tag == b"NULL":
            # No picture data: retain the previous frame unchanged.
            return False
        if p >= len(pkt):
            raise DxaError("frame header is truncated")
        comp = pkt[p]
        p += 1
        p += 4  # uncompressed payload length (big-endian u32)
        try:
            data = zlib.decompress(pkt[p:])
        except zlib.error:
            raise DxaError("compressed data is damaged")

        if comp in KEY_FULL:
            pic = self._full_frame(data, True)
            key = True
        elif comp in DELTA_FULL:
            pic = self._full_frame(data, False)
            key = False
        elif comp == BLOCK_KEY:
            pic = self._block_frame(data, True)
            key = True
        elif comp == BLOCK_DELTA:
            pic = self._block_frame(data, False)
            key = False
        else:
            raise DxaError("unknown compression %d" % comp)
        self.prev = bytes(pic)
        return key


def parse(data):
    if len(data) < 16 or data[:8] != MAGIC:
        raise DxaError("not a DXA frame sequence")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    count = int.from_bytes(data[12:16], "little")
    if not w or not h or w % 4 or h % 4:
        raise DxaError("width and height must be non-zero multiples of 4")
    dec = Decoder(w, h)
    pos, frames = 16, []
    for _ in range(count):
        if pos + 4 > len(data):
            raise DxaError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise DxaError("frame data is truncated")
        pos += 4 + n
        try:
            key = dec.frame(body)
            ok = True
        except DxaError:
            key, ok = False, False
        # An undecodable frame leaves the reference picture untouched.
        frames.append({"ok": ok, "key": key,
                       "crc32": "%08x" % zlib.crc32(dec.prev)})
    rgb = b"".join(bytes(dec.pal[v]) for v in dec.prev)
    return {"width": w, "height": h, "frames": frames,
            "colours": len(set(dec.prev)),
            "rgb_crc32": "%08x" % zlib.crc32(rgb)}