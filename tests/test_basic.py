import unittest

from rpzakit.rpza import RpzaError, _blend, decode, parse


def seq(w, h, frames):
    return b"RPZASEQ\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + len(frames).to_bytes(4, "little") \
        + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


def chunk(stream):
    # 0xE1 marker + 3-byte big-endian chunk length (header included).
    n = len(stream) + 4
    return bytes([0xE1, (n >> 16) & 255, (n >> 8) & 255, n & 255]) + stream


def pic_of(frame, w=8, h=8):
    stride = w
    pic = [0] * (stride * h)
    decode(pic, stride, w, h, chunk(frame))
    return pic


class BasicTests(unittest.TestCase):
    def test_single_fill(self):
        frame = bytes([0xA0, 0x7C, 0x00])
        out = parse(seq(4, 4, [chunk(frame)]))
        self.assertTrue(out["frames"][0]["ok"])
        self.assertEqual(out["colours"], 1)

    def test_bad_height(self):
        with self.assertRaises(RpzaError):
            parse(seq(4, 6, []))

    def test_fill_writes_rgb555_value(self):
        pic = pic_of(bytes([0xA0, 0x7C, 0x00]), 4, 4)
        self.assertTrue(all(v == 0x7C00 for v in pic))

    def test_blend_palette_weights_and_order(self):
        # Palette must be [A, (2A+B)/3, (A+2B)/3, B], two distinct mid colours.
        c = _blend(0x7C00, 0x001F)
        self.assertEqual(c[0], 0x7C00)
        self.assertEqual(c[3], 0x001F)
        self.assertNotEqual(c[1], c[2])
        # Red channel of the first mid colour: round((2*31 + 0)/3) = 21.
        self.assertEqual((c[1] >> 10) & 0x1F, 21)
        # Red channel of the second mid colour: round((1*31 + 2*0)/3) = 10.
        self.assertEqual((c[2] >> 10) & 0x1F, 10)

    def test_index_nibbles_run_left_to_right(self):
        # One blend block: palette colours A=0x7C00 and B=0x001F.
        # Index byte 0b00_01_10_11 must place palette entries 0,1,2,3 in
        # columns 0..3 (high pair first), not mirrored.
        frame = bytes([0xC1, 0x7C, 0x00, 0x00, 0x1F]) + bytes([0x1B] * 4)
        pic = pic_of(frame, 4, 4)
        c = _blend(0x7C00, 0x001F)
        self.assertEqual([pic[x] for x in range(4)], [c[0], c[1], c[2], c[3]])

    def test_row_end_block_is_not_dropped(self):
        # 8x4 picture = two blocks per row. Two fills must cover the whole
        # first row without losing the second block.
        frame = bytes([0xA1, 0x12, 0x34, 0xA1, 0x56, 0x78])
        pic = pic_of(frame, 8, 4)
        self.assertEqual(pic[0:4], [0x1234] * 4)
        self.assertEqual(pic[4:8], [0x5678] * 4)

    def test_skip_keeps_previous_frame_pixels(self):
        first = chunk(bytes([0xA1, 0x12, 0x34, 0x61]))  # fill block 0, skip block 1
        second = chunk(bytes([0x61, 0xA1, 0xAB, 0xCD]))  # skip block 0, fill block 1
        out = parse(seq(8, 4, [first, second]))
        self.assertTrue(all(f["ok"] for f in out["frames"]))
        stride = 8
        pic = [0] * (stride * 4)
        decode(pic, stride, 8, 4, first)
        decode(pic, stride, 8, 4, second)
        # Block 0 survives unchanged; block 1 gets the new colour.
        self.assertTrue(all(v == 0x1234 for v in pic[0:4]))
        self.assertTrue(all(v == 0xABCD for v in pic[4:8]))

    def test_sixteen_colour_block_pixels(self):
        colors = [0x0001 + i for i in range(16)]
        stream = bytes([0x20]) + b"".join(c.to_bytes(2, "big") for c in colors)
        pic = pic_of(stream, 4, 4)
        self.assertEqual(pic, colors)

    def test_truncated_sixteen_colour_block_keeps_rest(self):
        # Only 10 of 16 pixel colours are present in the chunk.
        colors = [0x0100 + i for i in range(10)]
        stream = bytes([0x2E]) + b"".join(c.to_bytes(2, "big") for c in colors)
        pic = pic_of(stream, 4, 4)
        self.assertEqual(pic[:10], colors)
        self.assertEqual(pic[10:], [0] * 6)  # unwritten pixels stay black

    def test_crc_is_stable_across_unchanged_frames(self):
        first = chunk(bytes([0xA1, 0x12, 0x34, 0x61]))
        second = chunk(bytes([0x62]))  # skip both blocks: picture identical
        out = parse(seq(8, 4, [first, second]))
        self.assertTrue(all(f["ok"] for f in out["frames"]))
        self.assertEqual(out["frames"][0]["crc32"], out["frames"][1]["crc32"])


if __name__ == "__main__":
    unittest.main()

