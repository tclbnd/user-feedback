import unittest

from astro.screen import AstroError, parse


class BasicTests(unittest.TestCase):
    def test_blank_screen(self):
        regs = bytes([0x11] * 8) + bytes([0, 0, 204]) + bytes(5)
        out = parse(b"ASTROVR\0" + regs + bytes(4096))
        self.assertEqual(out["first_row"], [["11", 160]])
        self.assertEqual(out["width"], 160)

    def test_not_a_snapshot(self):
        with self.assertRaises(AstroError):
            parse(b"ASTROVX\0" + bytes(100))


if __name__ == "__main__":
    unittest.main()
