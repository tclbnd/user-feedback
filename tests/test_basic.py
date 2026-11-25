import struct
import unittest

from xwdkit.xwd import XwdError, parse


def header(w, h, bpp, visual=3):
    return struct.pack(">25I", 100, 7, 2, bpp, w, h, 0, 1, 32, 1, 32, bpp, w * bpp // 8,
                       visual, 0, 0, 0, 8, 0, 0, w, h, 0, 0, 0)


class BasicTests(unittest.TestCase):
    def test_size(self):
        out = parse(header(4, 2, 8) + bytes(8))
        self.assertEqual((out["width"], out["height"], out["image_bytes"]), (4, 2, 8))

    def test_version(self):
        data = bytearray(header(4, 2, 8) + bytes(8))
        data[7] = 6
        with self.assertRaises(XwdError):
            parse(bytes(data))
