import struct
import unittest

from gemkit.img import ImgError, parse


class BasicTests(unittest.TestCase):
    def test_black_bar(self):
        head = struct.pack(">8H", 1, 8, 1, 2, 85, 85, 16, 1)
        out = parse(head + bytes([0x81, 0x01]))
        self.assertEqual(out["first_row"], "1111111100000000")

    def test_not_an_image(self):
        with self.assertRaises(ImgError):
            parse(b"\x00\x01")
