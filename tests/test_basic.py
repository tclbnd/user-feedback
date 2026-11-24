import struct
import unittest

from sgikit.sgi import SgiError, parse


class BasicTests(unittest.TestCase):
    def test_header(self):
        hd = struct.pack(">hBBHHHH", 474, 0, 1, 2, 2, 1, 1) + bytes(498) + b"\x05\x06"
        out = parse(hd)
        self.assertEqual((out["storage"], out["width"], out["channels"]), ("verbatim", 2, 1))

    def test_magic(self):
        with self.assertRaises(SgiError):
            parse(bytes(600))
