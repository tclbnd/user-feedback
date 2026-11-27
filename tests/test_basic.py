import unittest

from degaskit.degas import DegasError, parse


class BasicTests(unittest.TestCase):
    def test_blank_low_resolution(self):
        out = parse(bytes(2) + bytes(32) + bytes(32000))
        self.assertEqual((out["width"], out["height"], out["planes"]), (320, 200, 4))
        self.assertEqual(out["compressed"], False)

    def test_short_file(self):
        with self.assertRaises(DegasError):
            parse(bytes(10))


if __name__ == "__main__":
    unittest.main()
