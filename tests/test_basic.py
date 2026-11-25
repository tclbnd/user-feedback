import unittest

from hdrkit.hdr import HdrError, parse

HEAD = b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 1 +X 8\n"


class BasicTests(unittest.TestCase):
    def test_header(self):
        row = bytes([2, 2, 0, 8]) + bytes([136, 0]) * 4
        out = parse(HEAD + row)
        self.assertEqual((out["format"], out["width"], out["height"]), ("32-bit_rle_rgbe", 8, 1))

    def test_not_radiance(self):
        with self.assertRaises(HdrError):
            parse(b"P6\n1 1\n255\n\0\0\0")
