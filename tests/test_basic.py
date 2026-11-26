import unittest

from pmprc.render import PrcError, parse


class BasicTests(unittest.TestCase):
    def test_lcd_not_updated(self):
        out = parse(b"PMPRC\0\0\0" + bytes(16) + bytes(0x20000))
        self.assertEqual(out["first_row"], [["#fffb87", 96]])
        self.assertEqual(out["dark_pixels"], 0)

    def test_not_a_snapshot(self):
        with self.assertRaises(PrcError):
            parse(b"PMPRX\0\0\0" + bytes(100))


if __name__ == "__main__":
    unittest.main()
