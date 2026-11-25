import struct
import unittest

from patchkit.patch import PatchError, parse


def make_picture(width, height, left, top, columns):
    """Build a picture lump.

    columns is one list of posts per column; each post is (topdelta, pixels).
    """
    header = struct.pack("<HHhh", width, height, left, top)
    body_start = 8 + 4 * width
    blob = bytearray()
    offsets = []
    for posts in columns:
        offsets.append(body_start + len(blob))
        for delta, pixels in posts:
            blob += bytes([delta, len(pixels), 0]) + pixels + b"\0"
        blob.append(0xFF)
    return header + struct.pack("<%dI" % width, *offsets) + bytes(blob)


class BasicTests(unittest.TestCase):
    def test_empty_columns(self):
        data = struct.pack("<4h", 2, 4, 0, 0) + struct.pack("<2I", 16, 16) + b"\xff"
        out = parse(data)
        self.assertEqual((out["width"], out["posts"], out["opaque_pixels"]), (2, 0, 0))

    def test_not_picture(self):
        with self.assertRaises(PatchError):
            parse(b"\0\0")

    def test_offsets_are_signed(self):
        data = make_picture(1, 2, -1, -1, [[(0, b"\x05\x06")]])
        out = parse(data)
        self.assertEqual(out["offset"], [-1, -1])
        self.assertEqual(out["points"]["top_left"], 5)
        self.assertEqual(out["points"]["bottom_left"], 6)

    def test_posts_have_unused_and_pad_bytes(self):
        # post 1 covers rows 5-7, post 2 covers rows 10-11 of a 12px picture
        data = make_picture(1, 12, 0, 0, [[(5, b"\x01\x02\x03"), (10, b"\x04\x05")]])
        out = parse(data)
        self.assertEqual((out["posts"], out["opaque_pixels"], out["longest_post"]), (2, 5, 3))
        self.assertIsNone(out["points"]["top_left"])
        self.assertEqual(out["points"]["center"], 2)
        self.assertEqual(out["points"]["bottom_left"], 5)
        self.assertTrue(out["complete"])

    def test_tall_picture_continuation(self):
        # 300px picture: a 254px post followed by a zero-topdelta continuation
        data = make_picture(1, 300, 0, 0, [[(0, bytes(range(254))), (0, bytes([200]) * 46)]])
        out = parse(data)
        self.assertTrue(out["complete"])
        self.assertEqual(out["posts"], 2)
        self.assertEqual(out["longest_post"], 254)
        self.assertEqual(out["opaque_pixels"], 300)
        self.assertEqual(out["points"]["top_left"], 0)
        self.assertEqual(out["points"]["bottom_left"], 200)

    def test_truncated_post_is_incomplete(self):
        good = make_picture(1, 4, 0, 0, [[(0, b"\x01\x02\x03\x04")]])
        out = parse(good[:-3])
        self.assertFalse(out["complete"])

    def test_shared_columns(self):
        post = [(0, b"\x07\x08")]
        data = bytearray(make_picture(2, 2, 0, 0, [post, post]))
        struct.pack_into("<I", data, 12, struct.unpack_from("<I", data, 8)[0])
        out = parse(bytes(data))
        self.assertEqual(out["shared_columns"], 1)
        self.assertEqual(out["opaque_pixels"], 4)
