import unittest

from stic.backtab import SticError, parse


class BasicTests(unittest.TestCase):
    def test_blank_background(self):
        data = b"INTVBT\0\0" + bytes([1, 0, 0, 0, 0, 0, 0, 0]) + bytes(480 + 2048 + 512)
        out = parse(data)
        self.assertEqual(out["first_row"], [["#000000", 160]])
        self.assertEqual(out["cards"]["grom"], 240)

    def test_not_a_snapshot(self):
        with self.assertRaises(SticError):
            parse(b"INTVMO\0\0" + bytes(100))


if __name__ == "__main__":
    unittest.main()
