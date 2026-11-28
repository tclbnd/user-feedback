import unittest

from arbckit.arbc import ArbcError, parse


class BasicTests(unittest.TestCase):
    def test_example_header(self):
        with open("examples/screen.arbc", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (96, 64, 3))

    def test_bad_magic(self):
        with self.assertRaises(ArbcError):
            parse(bytes(16))


if __name__ == "__main__":
    unittest.main()
