import unittest

from pafkit.paf import PafError, parse


class BasicTests(unittest.TestCase):
    def test_example_frames(self):
        with open("examples/intro.paf", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (256, 32, 4))

    def test_bad_magic(self):
        with self.assertRaises(PafError):
            parse(bytes(16))


if __name__ == "__main__":
    unittest.main()
