import unittest

from pickit.pictor import PicError, parse


class BasicTests(unittest.TestCase):
    def test_raw_picture(self):
        head = (0x1234).to_bytes(2, "little") + (4).to_bytes(2, "little") + (2).to_bytes(2, "little") + bytes(4) + bytes([0x08])
        ext = b"\xFF\x00" + (0).to_bytes(2, "little") + (0).to_bytes(2, "little")
        out = parse(head + ext + bytes(2) + bytes(8))
        self.assertEqual((out["width"], out["height"]), (4, 2))

    def test_bad_magic(self):
        with self.assertRaises(PicError):
            parse(bytes(16))


if __name__ == "__main__":
    unittest.main()
