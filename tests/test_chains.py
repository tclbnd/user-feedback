import os
import unittest

from bfikit.bfi import BfiError, decode, palette, parse

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def pkt(chains):
    # 4-byte unpacked-size prefix (unused) followed by raw chain bytes
    return bytes(4) + b"".join(chains)


class OpcodeTests(unittest.TestCase):
    def test_raw_chain(self):
        pic = bytearray(8)
        state = decode(pic, pkt([bytes([0x04, 9, 8, 7, 6]), bytes([0x80, 0, 0])]))
        self.assertEqual(state, "decoded")
        self.assertEqual(pic[:4], bytes([9, 8, 7, 6]))
        self.assertEqual(pic[4:], bytes(4))

    def test_alternating_fill_uses_both_colours(self):
        pic = bytearray(8)
        # code 3, length 4 (8 pixels), colours 0x11, 0x22
        state = decode(pic, pkt([bytes([0xC4, 0x11, 0x22]), bytes([0x80, 0, 0])]))
        self.assertEqual(state, "decoded")
        self.assertEqual(list(pic), [0x11, 0x22] * 4)

    def test_skip_keeps_previous_frame(self):
        pic = bytearray(b"\xaa" * 8)
        # code 2 skip length 5, then code 0 raw length 3 fills the picture
        state = decode(pic, pkt([bytes([0x85]), bytes([0x03, 1, 2, 3])]))
        self.assertEqual(state, "decoded")
        self.assertEqual(list(pic), [0xAA] * 5 + [1, 2, 3])

    def test_end_code_stops_before_picture_fills(self):
        pic = bytearray(8)
        state = decode(pic, pkt([bytes([0x01, 0x55]), bytes([0x80, 0, 0])]))
        self.assertEqual(state, "decoded")
        self.assertEqual(pic[0], 0x55)
        self.assertEqual(pic[1:], bytes(7))

    def test_inline_back_copy(self):
        # four raw pixels position d at 4, then code 1 length 3 offset 4:
        # src = 4 - 4 = 0, copying pixels 0..2 over 4..6
        pic = bytearray(8)
        chains = [bytes([0x04, 1, 2, 3, 4]), bytes([0x43, 0x04]), bytes([0x80, 0, 0])]
        state = decode(pic, pkt(chains))
        self.assertEqual(state, "decoded")
        self.assertEqual(list(pic), [1, 2, 3, 4, 1, 2, 3, 0])

    def test_extended_back_copy(self):
        # ten raw pixels position d at 10; marker 0x40, length 6, offset
        # u16le 10 gives src = 10 - 10 = 0, copying 0..5 over 10..15
        pic = bytearray(16)
        head = bytes([0x0A]) + bytes(range(10))
        state = decode(pic, pkt([head, bytes([0x40, 0x06, 0x0A, 0x00])]))
        self.assertEqual(state, "decoded")
        self.assertEqual(list(pic), list(range(10)) + [0, 1, 2, 3, 4, 5])

    def test_overlapping_back_copy_unfolds(self):
        # seed pixel then length 5 offset 1 at d=1: src stays one behind,
        # so the run grows byte-by-byte (RLE-style) to fill the picture
        pic = bytearray(6)
        state = decode(pic, pkt([bytes([0x01, 7]), bytes([0x45, 0x01])]))
        self.assertEqual(state, "decoded")
        self.assertEqual(list(pic), [7] * 6)

    def test_chain_past_picture_stops(self):
        pic = bytearray(4)
        # code 2 skip length 8 would run past the 4-pixel picture
        state = decode(pic, pkt([bytes([0x80, 0x08, 0x00])]))
        self.assertEqual(state, "stopped")

    def test_back_chain_before_picture_is_error(self):
        pic = bytearray(4)
        with self.assertRaises(BfiError):
            decode(pic, pkt([bytes([0x42, 0x05])]))

    def test_truncated_frame_is_error(self):
        pic = bytearray(8)
        # raw chain wants 4 bytes but only 2 follow
        with self.assertRaises(BfiError):
            decode(pic, bytes([0, 0, 0, 0, 0x04, 1, 2]))


class PaletteTests(unittest.TestCase):
    def test_six_to_eight_expansion(self):
        # 0x00 -> 0x00; 0x3f -> 0xff; 0x12 -> (0x12<<2)|(0x12>>4) = 0x49
        self.assertEqual(palette(bytes([0x00, 0x3F, 0x12])), [(0x00, 0xFF, 0x49)])

    def test_expansion_replicates_top_bits(self):
        # 6-bit 0b101010 expands to 8-bit 0b10101010
        self.assertEqual(palette(bytes([0x2A, 0x00, 0x00]))[0][0], 0xAA)


class ExampleMovieTests(unittest.TestCase):
    def _parse(self, name):
        with open(os.path.join(HERE, "examples", name), "rb") as fh:
            return parse(fh.read())

    def test_every_example_frame_decodes(self):
        for name in ("intro.bfi", "death.bfi"):
            with self.subTest(name=name):
                out = self._parse(name)
                self.assertTrue(out["frames"])
                self.assertEqual({f["state"] for f in out["frames"]}, {"decoded"})


if __name__ == "__main__":
    unittest.main()
