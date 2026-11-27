import unittest

from yopkit.yop import Decoder, YopError, parse


def ysq(w, h, frames, ncol=0):
    return b"YOPSEQ\0\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes([ncol, 0, 0]) \
        + len(frames).to_bytes(4, "little") + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


class BasicTests(unittest.TestCase):
    def test_flat_block(self):
        frame = bytes([0, 0, 0, 0, 0xDD, 7])
        out = parse(ysq(2, 2, [frame]))
        self.assertTrue(out["frames"][0]["ok"])
        self.assertEqual(out["colours"], 1)

    def test_odd_width(self):
        with self.assertRaises(YopError):
            parse(ysq(3, 2, []))

    def test_paint_positions(self):
        # tag 0 paints the four pixels in reading order: TL TR / BL BR
        dec = Decoder(2, 2, 0, (0, 0))
        dec.frame(bytes([0, 0, 0, 0, 0x00, 1, 2, 3, 4]))
        self.assertEqual(list(dec.pic), [1, 2, 3, 4])

    def test_tag_nibble_order(self):
        # high nibble codes the first block, low nibble the second
        dec = Decoder(4, 2, 0, (0, 0))
        dec.frame(bytes([0, 0, 0, 0, 0x1D, 10, 20, 30, 40]))
        # tag 1: TL=c0 TR=c1 BL=c2 BR=c0; tag 13: whole block = c0
        self.assertEqual(list(dec.pic), [10, 20, 40, 40, 30, 10, 40, 40])

    def test_copy_block(self):
        # tag 15 copies the 2x2 block from (x + dx, y + dy), signed offsets
        dec = Decoder(4, 2, 0, (0, 0))
        dec.frame(bytes([0, 0, 0, 0, 0x0F, 1, 2, 3, 4, 0xFE, 0x00]))
        self.assertEqual(list(dec.pic), [1, 2, 1, 2, 3, 4, 3, 4])

    def test_odd_frame_palette(self):
        # the palette of an odd frame goes to the odd-frame colour segment
        dec = Decoder(2, 2, 1, (10, 20))
        dec.frame(bytes([1, 0, 0, 0, 0x10, 0x20, 0x30, 0xDD, 5]))
        self.assertEqual(dec.pal[20], (0x10 << 2) << 16 | (0x20 << 2) << 8 | (0x30 << 2))
        self.assertEqual(dec.pal[10], 0)


if __name__ == "__main__":
    unittest.main()
