import unittest

from sgrkit.sgr import SgrError, parse


class BasicTests(unittest.TestCase):
    def test_example_header(self):
        with open("examples/studio.sgr", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (64, 24, 1))

    def test_bad_magic(self):
        with self.assertRaises(SgrError):
            parse(bytes(16))


if __name__ == "__main__":
    unittest.main()
