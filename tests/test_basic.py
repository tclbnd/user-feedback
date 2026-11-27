import unittest
import zlib

from cramkit.cram import CramError, parse


def pak(w, h, depth, frames):
    return b"CRAMPAK\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes([depth]) \
        + len(frames).to_bytes(2, "little") + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


def crc8(pix):
    return "%08x" % zlib.crc32(bytes(pix))


def crc16(pix):
    return "%08x" % zlib.crc32(b"".join(v.to_bytes(2, "little") for v in pix))


class BasicTests(unittest.TestCase):
    def test_single_fill(self):
        out = parse(pak(4, 4, 8, [bytes([7, 0x80])]))
        self.assertEqual(out["frames"][0]["fill"], 1)
        self.assertEqual(out["frames"][0]["crc32"], crc8([7] * 16))

    def test_bad_depth(self):
        with self.assertRaises(CramError):
            parse(pak(4, 4, 24, []))

    def test_blocks_are_coded_bottom_up(self):
        # first block in the stream is the bottom row of the picture
        out = parse(pak(4, 8, 8, [bytes([1, 0x80, 2, 0x80])]))
        self.assertEqual(out["frames"][0]["crc32"], crc8([2] * 16 + [1] * 16))

    def test_two_color_bit_selects_second_color(self):
        # flags = 1: only the first coded pixel (bottom left) uses color 1
        out = parse(pak(4, 4, 8, [bytes([1, 0, 10, 20])]))
        self.assertEqual(out["frames"][0]["two"], 1)
        self.assertEqual(out["frames"][0]["crc32"],
                         crc8([10] * 12 + [20, 10, 10, 10]))

    def test_fill_marker_up_to_0x8f(self):
        # 0x88-0x8f (outside the 0x84-0x87 skip codes) are 1-color blocks
        out = parse(pak(4, 4, 8, [bytes([9, 0x88])]))
        self.assertEqual(out["frames"][0]["fill"], 1)
        self.assertEqual(out["frames"][0]["crc32"], crc8([9] * 16))

    def test_skipped_blocks_keep_previous_frame(self):
        out = parse(pak(4, 4, 8, [bytes([5, 0x80]), bytes([1, 0x84])]))
        self.assertEqual(out["frames"][1]["skip"], 1)
        self.assertEqual(out["frames"][1]["crc32"], crc8([5] * 16))

    def test_16bit_eight_color_block(self):
        # flags word 0, then 8 RGB555 colours; bit 15 of the first marks 8-colour
        colors = [0x8000, 1, 2, 3, 4, 5, 6, 7]
        frame = bytes([0, 0]) + b"".join(c.to_bytes(2, "little") for c in colors)
        out = parse(pak(4, 4, 16, [frame]))
        self.assertEqual(out["frames"][0]["quad"], 1)
        pix = [2, 2, 6, 6] * 2 + [0x8000, 0x8000, 4, 4] * 2
        self.assertEqual(out["frames"][0]["crc32"], crc16(pix))

    def test_short_chunk(self):
        out = parse(pak(8, 4, 8, [bytes([7, 0x80])]))
        self.assertTrue(out["frames"][0]["short"])


if __name__ == "__main__":
    unittest.main()
