import struct
import unittest

from raskit.raster import RasterError, parse


def grey(w, h, pixels):
    head = struct.pack(">8I", 0x59A66A95, w, h, 8, len(pixels), 1, 0, 0)
    return head + bytes(pixels)


class BasicTests(unittest.TestCase):
    def test_grey_image(self):
        out = parse(grey(2, 1, [0, 255]))
        self.assertEqual(out["first_row"], ["#000000", "#ffffff"])
        self.assertTrue(out["complete"])

    def test_not_a_raster(self):
        with self.assertRaises(RasterError):
            parse(b"\x00" * 40)
