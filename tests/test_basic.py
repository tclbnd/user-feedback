import unittest

from pnmkit.pnm import PnmError, parse


class BasicTests(unittest.TestCase):
    def test_ppm_size(self):
        out = parse(b"P6\n2 1\n255\n" + bytes([1, 2, 3, 4, 5, 6]))
        self.assertEqual((out["images"][0]["width"], out["images"][0]["height"]), (2, 1))

    def test_bad_magic(self):
        with self.assertRaises(PnmError):
            parse(b"XX 1 1")


if __name__ == "__main__":
    unittest.main()
