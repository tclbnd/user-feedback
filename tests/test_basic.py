import unittest

from mszhkit.mszh import MszhError, parse


class BasicTests(unittest.TestCase):
    def test_example_header(self):
        with open("examples/intro.mszh", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], out["type"], len(out["frames"])), (48, 16, "yuv422", 3))

    def test_bad_magic(self):
        with self.assertRaises(MszhError):
            parse(bytes(32))


if __name__ == "__main__":
    unittest.main()
