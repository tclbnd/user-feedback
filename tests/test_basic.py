import unittest

from tedgfx.bitmap import TedError, parse


def blank(mode06=0x38):
    r = bytearray(32)
    r[0x06] = mode06
    return b"TEDSNAP\0" + bytes(r) + bytes(65536)


class BasicTests(unittest.TestCase):
    def test_blank_bitmap(self):
        out = parse(blank())
        self.assertEqual(out["first_row"], [["00", 320]])
        self.assertEqual(out["mode"], "hires")

    def test_character_mode_rejected(self):
        with self.assertRaises(TedError):
            parse(blank(0x18))


if __name__ == "__main__":
    unittest.main()
