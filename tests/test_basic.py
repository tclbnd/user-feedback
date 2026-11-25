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

    def test_signed_offset(self):
        # sprites centred above/left of the origin have negative offsets
        data = struct.pack("<4h", 2, 4, -3, -200) + struct.pack("<2I", 12, 12) + b"\xff"
        out = parse(data)
        self.assertEqual(out["offset"], [-3, -200])

    def test_post_layout(self):
        # one column, one post: topdelta 2, 3 pixels, padding around the data
        col = bytes([2, 3, 0, 10, 20, 30, 0, 0xFF])
        data = struct.pack("<4h", 1, 8, 0, 0) + struct.pack("<I", 12) + col
        out = parse(data)
        self.assertTrue(out["complete"])
        self.assertEqual((out["posts"], out["opaque_pixels"], out["longest_post"]), (1, 3, 3))
        self.assertIsNone(out["points"]["top_left"])
        self.assertEqual(out["points"]["center"], 30)  # (0, 4) is the post's last pixel

    def test_tall_patch(self):
        # 1x300 tall picture: one tall post (16-bit topdelta) from y=290, 10 pixels
        col = struct.pack("<HB", 290, 10) + b"\x00" + bytes(range(10)) + b"\x00" + b"\xff\xff"
        data = struct.pack("<4h", 1, 300, 0, 0) + struct.pack("<I", 12) + col
        out = parse(data)
        self.assertTrue(out["complete"])
        self.assertEqual((out["posts"], out["opaque_pixels"], out["longest_post"]), (1, 10, 10))
        self.assertEqual(out["points"]["bottom_right"], 9)  # (0, 299)
        self.assertIsNone(out["points"]["top_left"])

    def test_truncated_column(self):
        data = struct.pack("<4h", 1, 4, 0, 0) + struct.pack("<I", 12) + bytes([1, 5, 0, 7])
        out = parse(data)
        self.assertFalse(out["complete"])
