import unittest

from hnmkit.hnm4 import HnmError, Decoder, parse


def seq(version, w, h, chunks):
    return b"HNM4SEQ\0" + bytes([version, 0]) + w.to_bytes(2, "little") + h.to_bytes(2, "little") \
        + len(chunks).to_bytes(2, "little") + b"".join(len(c).to_bytes(4, "little") + c for c in chunks)


class BitBuilder:
    """Pack control bits (LSB-first 32-bit words) and a separate data stream."""

    def __init__(self):
        self.cbits = []
        self.data = bytearray()

    def bits(self, values, width):
        for v in values:
            for i in range(width):
                self.cbits.append((v >> i) & 1)

    def literal(self, pixel):
        self.bits([1], 1)
        self.data.append(pixel)

    def short(self, code, delta_byte):
        self.bits([0, 0, code & 1, (code >> 1) & 1], 1)
        self.data.append(delta_byte)

    def long(self, count6, dist10, extra=None):
        self.bits([0, 1], 1)
        word = (count6 << 10) | (dist10 & 0x3FF)
        self.data += word.to_bytes(2, "little")
        if extra is not None:
            self.data.append(extra)

    def build(self):
        while len(self.cbits) % 32:
            self.cbits.append(0)
        ctrl = bytearray()
        for i in range(0, len(self.cbits), 32):
            word = 0
            for j, b in enumerate(self.cbits[i:i + 32]):
                word |= b << j
            ctrl += word.to_bytes(4, "little")
        return bytes(ctrl + self.data)


class BasicTests(unittest.TestCase):
    def test_sound_chunk_skipped(self):
        out = parse(seq(0x48, 8, 4, [bytes([12, 0, 0, 0]) + b"SD" + bytes(6)]))
        self.assertEqual(out["skipped"], 1)
        self.assertEqual(out["frames"], [])

    def test_odd_height(self):
        with self.assertRaises(HnmError):
            parse(seq(0x48, 8, 5, []))

    def test_literal_and_short_delta(self):
        # 4x2 frame (two fields of 4 pixels each); fill all 8 linearly.
        bb = BitBuilder()
        bb.literal(10)
        bb.short(0, 5)    # count = 0 + 1 = 1, +5 -> 15
        bb.literal(20)
        bb.short(1, 251)  # count = 1 + 1 = 2, delta -5 -> 15, 10
        # second field starts at offset 4
        bb.literal(100)
        bb.short(2, 2)    # count = 2 + 1 = 3, +2 -> 102,104,106
        dec = Decoder(0x48, 4, 2)
        dec.intra(bb.build())
        flat = bytes(dec.cur)
        self.assertEqual(flat, bytes([10, 15, 20, 15, 10, 100, 102, 104]))
        # picture() re-interleaves even/odd fields into display scan lines
        self.assertEqual(dec.picture(),
                         bytes([10, 15, 20, 15, 10, 100, 102, 104]))

    def test_interleaved_fields_are_reordered(self):
        # 2x2: field0 (even line) = [1, 2], field1 (odd line) = [3, 4]
        bb = BitBuilder()
        bb.literal(1)
        bb.literal(2)
        bb.literal(3)
        bb.literal(4)
        dec = Decoder(0x48, 2, 2)
        dec.intra(bb.build())
        # linear buffer keeps fields concatenated; picture() re-interleaves
        self.assertEqual(bytes(dec.cur), bytes([1, 2, 3, 4]))
        self.assertEqual(dec.picture(), bytes([1, 2, 3, 4]))
        # taller frame to prove row reordering: 2 wide, 4 tall (fields of 4)
        bb = BitBuilder()
        # field 0 (even rows 0,2): 1,2,3,4 ; field 1 (odd rows 1,3): 5,6,7,8
        for px in (1, 2, 3, 4, 5, 6, 7, 8):
            bb.literal(px)
        dec = Decoder(0x48, 2, 4)
        dec.intra(bb.build())
        # display order: row0=field0[0:2], row1=field1[0:2], row2, row3
        self.assertEqual(dec.picture(),
                         bytes([1, 2, 5, 6, 3, 4, 7, 8]))

    def test_long_copy_count_bias_and_distance(self):
        bb = BitBuilder()
        # establish four literals, then copy them back with distance 4.
        for px in (1, 2, 3, 4):
            bb.literal(px)
        # count6 = 2 -> actual count 4 (stored value is two short); dist = 4
        bb.long(2, 4)
        dec = Decoder(0x48, 8, 2)
        # pad remaining pixels (need 16 total) with literals
        for px in range(8):
            bb.literal(0)
        dec.intra(bb.build())
        self.assertEqual(bytes(dec.cur[0:8]), bytes([1, 2, 3, 4, 1, 2, 3, 4]))

    def test_frame_buffer_is_persistent(self):
        # First frame fills the whole linear buffer; a second intra writes only
        # its first literal, so the other stored pixels are retained.
        bb1 = BitBuilder()
        for px in range(8):
            bb1.literal(px + 1)
        dec = Decoder(0x48, 4, 2)
        dec.intra(bb1.build())
        first_linear = bytes(dec.cur)

        bb2 = BitBuilder()
        bb2.literal(99)  # only the first pixel is overwritten
        dec.intra(bb2.build())
        second_linear = bytes(dec.cur)
        self.assertEqual(second_linear[0], 99)
        self.assertEqual(second_linear[1:], first_linear[1:])


if __name__ == "__main__":
    unittest.main()

