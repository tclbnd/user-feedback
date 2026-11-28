import unittest

from sgrkit.sgr import SgrError, bgr8, frame, parse


class BasicTests(unittest.TestCase):
    def test_example_header(self):
        with open("examples/studio.sgr", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (64, 24, 1))

    def test_bad_magic(self):
        with self.assertRaises(SgrError):
            parse(bytes(16))


class DecodeTests(unittest.TestCase):
    def test_pixel_channels_repacked(self):
        # Stored RBG323: red in the top 3 bits, blue in the middle 2,
        # green in the bottom 3. BGR8 wants blue on top, then green, red.
        self.assertEqual(bgr8(0x00), 0x00)
        self.assertEqual(bgr8(0xE0), 0x07)  # pure red
        self.assertEqual(bgr8(0x07), 0x38)  # pure green
        self.assertEqual(bgr8(0x18), 0xC0)  # pure blue
        self.assertEqual(bgr8(0xFF), 0xFF)

    def test_run_longer_than_127_is_still_a_run(self):
        self.assertEqual(frame(bytes([191, 0xE0]), 191, 1), bytes([bgr8(0xE0)]) * 191)

    def test_run_wraps_across_scanlines(self):
        self.assertEqual(list(frame(bytes([6, 0x03]), 4, 2)), [bgr8(0x03)] * 6 + [0, 0])

    def test_raw_span_has_no_extra_byte(self):
        # 0xC3 = three raw pixels, then the next opcode follows immediately.
        got = frame(bytes([0xC3, 0xE0, 0x1C, 0x03]), 3, 1)
        self.assertEqual(list(got), [bgr8(0xE0), bgr8(0x1C), bgr8(0x03)])

    def test_truncated_raw_span_raises(self):
        with self.assertRaises(SgrError):
            frame(bytes([0xC5, 0x00, 0x00]), 5, 1)


if __name__ == "__main__":
    unittest.main()
