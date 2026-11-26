import struct
import unittest

from ndstex.texture import TexError, parse


class BasicTests(unittest.TestCase):
    def test_direct_colour(self):
        prm = 7 << 26  # direct colour, 8 x 8
        tex = struct.pack("<H", 0x801F) * 64
        out = parse(b"NDSTEX\0\0" + struct.pack("<IHxxIII", prm, 0, len(tex), 0, 0) + tex)
        self.assertEqual(out["first_row"], [["#ff0000ff", 8]])
        self.assertEqual(out["format"], "direct")

    def test_not_a_capture(self):
        with self.assertRaises(TexError):
            parse(b"NDSTXX\0\0" + bytes(40))


if __name__ == "__main__":
    unittest.main()
