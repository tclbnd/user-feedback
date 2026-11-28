"""Gryphon ARBC screen video: colour segments painting masked tiles at five block sizes over the previous frame."""
import struct
import zlib

# Flag bit -> tile edge length in pixels. A tile mask always addresses a 4x4
# grid of sub-tiles, so each sub-tile is (edge / 4) pixels square.
SIZES = ((0x01, 4), (0x02, 8), (0x04, 16), (0x08, 32), (0x10, 64))
CELLS = 4


class ArbcError(ValueError):
    pass


class In:
    def __init__(self, d, p=0):
        self.d, self.p = d, p

    def left(self):
        return len(self.d) - self.p

    def byte(self):
        v = self.d[self.p] if self.p < len(self.d) else 0
        self.p += 1
        return v

    def le16(self):
        return self.byte() | self.byte() << 8


class Arbc:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.prev = None

    def tiles(self, g, pix, size, color):
        w, h = self.w, self.h
        n = g.le16()
        scale = size // CELLS
        for _ in range(n):
            row, col = g.byte(), g.byte()
            mask = g.le16()
            # Rows are stored bottom row first; convert the tile origin to
            # top-down coordinates.
            sx = col * size
            sy = h - (row + 1) * size
            # A tile is only painted when it fits inside the picture entirely;
            # tiles crossing any edge are ignored by the codec.
            if sx < 0 or sy < 0 or sx + size > w or sy + size > h:
                continue
            for cr in range(CELLS):
                for cc in range(CELLS):
                    if not (mask >> (cr * CELLS + cc)) & 1:
                        continue
                    # Mask bit 0 is the bottom-right sub-tile, so both axes
                    # run from the bottom-right corner backwards.
                    top = sy + (CELLS - 1 - cr) * scale
                    left = sx + (CELLS - 1 - cc) * scale
                    for j in range(top, top + scale):
                        row_off = j * w
                        for k in range(left, left + scale):
                            pix[row_off + k] = color

    def packet(self, pkt):
        if len(pkt) < 8:
            raise ArbcError("packet is too short")
        g = In(pkt, 8)
        n = g.le16()
        # Every packet paints on top of the previous frame; only the very
        # first frame starts from a black picture.
        if self.prev is None:
            pix = [(0, 0, 0)] * (self.w * self.h)
        else:
            pix = self.prev
        if n == 0:
            # A packet with no segments produces no picture of its own.
            self.prev = pix
            return None
        for _ in range(n):
            # Each channel is one byte followed by a padding byte (stored as
            # a little-endian u16), then one flags byte.
            r, gg, b = g.le16() & 0xFF, g.le16() & 0xFF, g.le16() & 0xFF
            flag = g.byte()
            for bit, size in SIZES:
                if flag & bit:
                    self.tiles(g, pix, size, (r, gg, b))
        self.prev = pix
        return pix


def parse(data):
    if data[:8] != b"ARBCPACK":
        raise ArbcError("not an ARBC pack")
    w, h, count = struct.unpack_from("<HHH", data, 8)
    if not w or not h:
        raise ArbcError("empty picture")
    v = Arbc(w, h)
    pos = 14
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise ArbcError("packet table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        pkt = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(pkt) < n:
            raise ArbcError("packet is truncated")
        try:
            pix = v.packet(pkt)
        except ArbcError:
            frames.append({"state": "error", "crc32": None})
            continue
        if pix is None:
            frames.append({"state": "skipped"})
            continue
        frames.append({"state": "decoded", "crc32": "%08x" % zlib.crc32(b"".join(bytes(p) for p in pix))})
    return {"width": w, "height": h, "frames": frames}
