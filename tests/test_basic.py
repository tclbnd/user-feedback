import os
import struct
import unittest

from artkit.art import ArtError, parse

EXAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples")


def make_art(start, widths, heights, picanms, pixels=b""):
    """Build a tile archive: header, size/picanm tables, then pixel data."""
    n = len(widths)
    return (struct.pack("<4I", 1, 0, start, start + n - 1)
            + struct.pack("<%dh" % n, *widths)
            + struct.pack("<%dh" % n, *heights)
            + struct.pack("<%dI" % n, *picanms)
            + pixels)


class BasicTests(unittest.TestCase):
    def test_empty_slot(self):
        data = struct.pack("<4I", 1, 0, 0, 0) + struct.pack("<hhI", 0, 0, 0)
        out = parse(data)
        self.assertEqual((out["nonempty"], out["pixel_bytes"]), (0, 0))

    def test_not_art(self):
        with self.assertRaises(ArtError):
            parse(struct.pack("<4I", 2, 0, 0, 0))


class FormatTests(unittest.TestCase):
    def test_tile_count_is_inclusive(self):
        out = parse(make_art(0, [2, 0, 1], [2, 0, 1], [0, 0, 0], bytes(range(5))))
        self.assertEqual((out["first_tile"], out["last_tile"], out["tiles"]), (0, 2, 3))
        self.assertEqual(out["nonempty"], 2)

    def test_tile_numbers_start_at_header_start(self):
        out = parse(make_art(256, [1, 1, 1], [1, 1, 1], [0, 0, 0], bytes([1, 2, 3])))
        self.assertEqual([t["tile"] for t in out["tile_list"]], [256, 257, 258])

    def test_pixel_data_is_column_major(self):
        # 2 wide, 3 tall: bytes go down column 0, then down column 1.
        px = bytes([0, 1, 2, 3, 4, 5])
        out = parse(make_art(0, [2], [3], [0], px))
        tile = out["tile_list"][0]
        self.assertEqual(tile["top_right"], 3)   # column 1, row 0
        self.assertEqual(tile["bottom_left"], 2)  # column 0, row 2

    def test_offset_is_signed(self):
        # frames bits 0-5, animation type bits 6-7, x/y offset bytes, speed byte.
        a = 5 | (1 << 6) | (0xBE << 8) | (0xF0 << 16) | (9 << 24)
        tile = parse(make_art(0, [1], [1], [a], bytes([7])))["tile_list"][0]
        self.assertEqual(tile["offset"], [-66, -16])
        self.assertEqual((tile["frames"], tile["animation"], tile["speed"]),
                         (5, "oscillate", 9))

    def test_truncated_pixels(self):
        out = parse(make_art(0, [2], [2], [0], bytes([10, 20])))
        tile = out["tile_list"][0]
        self.assertFalse(out["complete"])
        self.assertIsNone(tile["transparent"])
        self.assertIsNone(tile["top_right"])
        self.assertEqual(tile["bottom_left"], 20)


class SampleTests(unittest.TestCase):
    def load(self, name):
        with open(os.path.join(EXAMPLES, name), "rb") as fh:
            return parse(fh.read())

    def test_tiles000(self):
        out = self.load("tiles000.art")
        self.assertEqual((out["first_tile"], out["last_tile"], out["tiles"]), (0, 3, 4))
        self.assertEqual((out["nonempty"], out["pixel_bytes"], out["complete"]), (3, 5120, True))
        t1, t3 = out["tile_list"][1], out["tile_list"][2]
        self.assertEqual((t1["tile"], t1["width"], t1["height"]), (1, 32, 16))
        self.assertEqual(t1["offset"], [-4, 2])
        self.assertEqual((t1["transparent"], t1["bottom_left"]), (64, 255))
        self.assertEqual(t3["tile"], 3)
        self.assertEqual((t3["frames"], t3["animation"], t3["speed"]), (3, "forward", 4))
        self.assertEqual((t3["top_right"], t3["bottom_left"]), (150, 31))

    def test_tiles001(self):
        out = self.load("tiles001.art")
        self.assertEqual((out["tiles"], out["nonempty"], out["pixel_bytes"]), (2, 2, 592))
        t = out["tile_list"][0]
        self.assertEqual((t["tile"], t["width"], t["height"]), (256, 8, 24))
        self.assertEqual((t["frames"], t["animation"], t["speed"]), (7, "oscillate", 2))
        self.assertEqual(t["offset"], [0, -12])
        self.assertEqual((t["transparent"], t["top_right"], t["bottom_left"]), (24, 207, 255))

