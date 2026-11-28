"""Tiertex SEQ video frames (.tsq): decode each frame to a 256 x 128 palette picture.

Frame layout (after the flags byte):
  * flag 3: a 768-byte 18-bit palette (256 entries, 6 bits per channel) followed
    by a 128-byte block map.
  * flag 1: a 128-byte block map only (the palette is kept from the previous
    frame).
  * flag 2: no map; the stream is a run of pixel-list blocks applied to grid
    blocks in row-major order.
  A block map holds 512 two-bit entries, four per map byte packed LSB-first
  (bits 0-1, 2-3, 4-5, 6-7), one entry per 8x8 block in row-major grid order.
  The block data stream then holds one variable-length record per non-skip
  map entry.

Block-map entry meanings:
  0 = skip (the block keeps its previous contents),
  1 = encoded block (colour-table or run-length),
  2 = raw block (64 literal pixel bytes),
  3 = sparse pixel-list block.

Encoded block header byte:
  * high bit clear -> colour-table block.  A palette table is followed by 64
    MSB-first packed indices.  The Tiertex encoder rounds the table size up to
    a power of two and the index width is log2 of that size; a compact encoder
    variant stores exactly the needed entries instead.  A zero header is a
    solid colour block holding a single byte.
  * high bit set -> run-length block.  The low two bits select the raster
    direction: 1 = vertical (column-major), 2 = horizontal (row-major).  An
    MSB-first nibble table follows, padded up to a byte boundary.  Each 4-bit
    signed nibble describes one run: a negative value repeats one byte that
    many times, a positive value emits that many literal bytes, and nibble
    zero is a full run of eight.  Nibbles are consumed until 64 pixels are
    produced and the final run is clipped to the block.

Pixel-list blocks hold (position, value) pairs.  Bit 7 of the position byte
marks the final entry; the low seven bits are the linear, row-major index of
the pixel inside the 8x8 block (x = index & 7, y = index >> 3).
"""
import zlib

MAGIC = b"TSEQVID\0"
W, H = 256, 128
BS = 8
NBX = W // BS


class SeqError(ValueError):
    pass


class Bits:
    def __init__(self, data, start=0):
        self.d, self.bit = data, start * 8

    def get(self, n):
        v = 0
        for _ in range(n):
            v = v << 1 | ((self.d[self.bit >> 3] >> (7 - (self.bit & 7))) & 1)
            self.bit += 1
        return v


def expand6(c):
    """Expand a 6-bit DAC channel to 8 bits by replicating the top two bits."""
    return (c << 2) | (c >> 4)


class Decoder:
    def __init__(self):
        self.pic = bytearray(W * H)
        self.pal = [0] * 256
        # Colour-table sizing convention used by the current frame's encoder.
        # "pow2" rounds the table up to a power of two (Tiertex SEQ); "compact"
        # stores exactly the needed entries (a smaller encoder variant).
        self.color_mode = "pow2"

    def put(self, bx, by, x, y, v):
        self.pic[(by + y) * W + (bx + x)] = v

    def _decode_encoded(self, d, p, end, bx, by):
        """Decode a colour-table or run-length block; return the new offset."""
        if p >= end:
            raise SeqError("block data runs out")
        header = d[p]
        p += 1
        if not (header & 0x80):
            # Colour-table block: a palette table followed by 64 packed indices.
            if header == 0:
                # A zero header is a solid-colour block whose single colour is
                # the next byte; there are no index bits.
                if end - p < 1:
                    raise SeqError("solid colour block runs out")
                value = d[p]
                for i in range(64):
                    y, x = divmod(i, BS)
                    self.put(bx, by, x, y, value)
                return p + 1
            n_a = header
            w_a = (header - 1).bit_length()
            n_b = header + 1
            w_b = header.bit_length()
            size_c = 1
            while size_c <= header:
                size_c <<= 1
            w_c = size_c.bit_length() - 1
            if self.color_mode == "pow2":
                raw_candidates = [(size_c, w_c), (n_a, w_a), (n_b, w_b)]
            else:
                raw_candidates = [(n_a, w_a), (n_b, w_b), (size_c, w_c)]
            candidates = []
            for cand in raw_candidates:
                if cand not in candidates:
                    candidates.append(cand)

            last_err = None
            for n, width in candidates:
                stream_bytes = (64 * width + 7) // 8
                if end - p < n + stream_bytes:
                    continue
                table = d[p:p + n]
                sp = p + n
                bits = Bits(d, sp)
                indices = [bits.get(width) for _ in range(64)]
                if max(indices) >= n:
                    last_err = SeqError("colour index out of range")
                    continue
                p = sp + stream_bytes
                for i, idx in enumerate(indices):
                    y, x = divmod(i, BS)
                    self.put(bx, by, x, y, table[idx])
                return p
            # A frame may be truncated inside its final block; consume whatever
            # bytes remain rather than failing the whole frame.
            if end - p <= 0:
                return p
            return end

        vertical = (header & 3) == 1
        table_start = p
        bits = Bits(d, p)
        runs = []
        produced = 0
        while produced < 64:
            nibble = bits.get(4)
            if nibble == 0:
                length, is_run = 8, True
            else:
                code = nibble - 16 if nibble & 8 else nibble
                length, is_run = abs(code), code < 0
            runs.append((length, is_run))
            produced += length
        p = table_start + (bits.bit - table_start * 8 + 7) // 8

        linear = []
        remaining = 64
        for length, is_run in runs:
            length = min(length, remaining)
            if length == 0:
                break
            if is_run:
                if p >= end:
                    raise SeqError("RLE data runs out")
                linear.extend([d[p]] * length)
                p += 1
            else:
                if end - p < length:
                    raise SeqError("RLE data runs out")
                linear.extend(d[p:p + length])
                p += length
            remaining -= length
        if len(linear) != 64:
            raise SeqError("RLE block is short")

        if vertical:
            for k, v in enumerate(linear):
                x, y = divmod(k, BS)
                self.put(bx, by, x, y, v)
        else:
            for k, v in enumerate(linear):
                y, x = divmod(k, BS)
                self.put(bx, by, x, y, v)
        return p

    def _render_map(self, d, p, end, ops):
        for index, op in enumerate(ops):
            if op == 0:
                continue
            by = (index // NBX) * BS
            bx = (index % NBX) * BS
            if op == 1:
                p = self._decode_encoded(d, p, end, bx, by)
            elif op == 2:
                if end - p < 64:
                    raise SeqError("raw block runs out")
                for i in range(64):
                    y, x = divmod(i, BS)
                    self.put(bx, by, x, y, d[p + i])
                p += 64
            else:  # op == 3: sparse pixel list
                while True:
                    if end - p < 2:
                        # A frame may end on a truncated final pixel list.
                        return p
                    pos, value = d[p], d[p + 1]
                    p += 2
                    location = pos & 0x7F
                    if location < 64:
                        y, x = divmod(location, BS)
                        self.put(bx, by, x, y, value)
                    if pos & 0x80:
                        break
        return p

    def frame(self, d):
        if not d:
            raise SeqError("empty frame")
        end = len(d)
        flags, p = d[0], 1
        # Flag semantics: 3 = palette + block map, 1 = block map only,
        # 2 = unmapped run of pixel-list blocks (no map).
        if flags == 3:
            if end - p < 768:
                raise SeqError("palette is truncated")
            for k in range(256):
                r = expand6(d[p])
                g = expand6(d[p + 1])
                b = expand6(d[p + 2])
                self.pal[k] = (r << 16) | (g << 8) | b
                p += 3
        has_map = flags in (1, 3)
        if has_map:
            if end - p < 128:
                raise SeqError("block map is truncated")
            map_bytes = d[p:p + 128]
            p += 128
            ops = []
            for byte in map_bytes:
                for shift in (0, 2, 4, 6):
                    ops.append((byte >> shift) & 3)
            # Two colour-table encodings exist.  The Tiertex SEQ encoder rounds
            # the table up to a power of two; a compact variant stores only the
            # needed entries.  Pick the mode whose byte consumption best covers
            # the frame's data stream.
            saved_pic = bytes(self.pic)
            best = None
            for mode in ("pow2", "compact"):
                self.color_mode = mode
                self.pic = bytearray(saved_pic)
                try:
                    consumed = self._render_map(d, p, end, ops)
                except SeqError:
                    continue
                score = abs(end - consumed)
                if best is None or score < best[0]:
                    best = (score, mode, consumed)
            if best is None:
                self.color_mode = "pow2"
                self.pic = bytearray(saved_pic)
                self._render_map(d, p, end, ops)
            else:
                self.color_mode = best[1]
                self.pic = bytearray(saved_pic)
                self._render_map(d, p, end, ops)
        elif flags & 2:
            # No block map: the remaining stream is a run of pixel-list blocks
            # applied to grid blocks in row-major order, each terminated by a
            # position byte with its high bit set.
            grid = 0
            while p < end:
                by = (grid // NBX) * BS
                bx = (grid % NBX) * BS
                while p + 1 < end:
                    pos, value = d[p], d[p + 1]
                    p += 2
                    location = pos & 0x7F
                    if location < 64:
                        y, x = divmod(location, BS)
                        self.put(bx, by, x, y, value)
                    if pos & 0x80:
                        break
                grid += 1


def parse(data):
    if len(data) < 12 or data[:8] != MAGIC:
        raise SeqError("not a Tiertex SEQ frame file")
    count = int.from_bytes(data[8:12], "little")
    dec = Decoder()
    pos, frames = 12, []
    for _ in range(count):
        if pos + 4 > len(data):
            raise SeqError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise SeqError("frame data is truncated")
        pos += 4 + n
        try:
            dec.frame(body)
            ok = True
        except SeqError:
            ok = False
        frames.append({"ok": ok, "crc32": "%08x" % zlib.crc32(bytes(dec.pic))})
    rgb = b"".join(dec.pal[v].to_bytes(3, "big") for v in dec.pic)
    return {"frames": frames, "colours": len(set(dec.pic)), "rgb_crc32": "%08x" % zlib.crc32(rgb)}
