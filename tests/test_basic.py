import struct
import unittest

from patchkit.patch import PatchError, parse


class BasicTests(unittest.TestCase):
    def test_empty_columns(self):
        data = struct.pack("<4h", 2, 4, 0, 0) + struct.pack("<2I", 16, 16) + b"\xff"
        out = parse(data)
        self.assertEqual((out["width"], out["posts"], out["opaque_pixels"]), (2, 0, 0))

    def test_not_picture(self):
        with self.assertRaises(PatchError):
            parse(b"\0\0")
