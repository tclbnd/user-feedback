import unittest

from dsvkit.dsv import DsvError, parse


class BasicTests(unittest.TestCase):
    def test_example_header(self):
        with open("examples/intro.dsv", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (48, 20, 4))

    def test_bad_magic(self):
        with self.assertRaises(DsvError):
            parse(bytes(32))


if __name__ == "__main__":
    unittest.main()
