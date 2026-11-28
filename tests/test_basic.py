import unittest

from spreskit.spres import SpresError, parse


class BasicTests(unittest.TestCase):
    def test_example_frames(self):
        with open("examples/tutorial.spr", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (40, 24, 3))

    def test_bad_magic(self):
        with self.assertRaises(SpresError):
            parse(bytes(16))


if __name__ == "__main__":
    unittest.main()
