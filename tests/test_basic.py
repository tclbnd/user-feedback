import unittest

from gdvkit.gdv import GdvError, parse


class BasicTests(unittest.TestCase):
    def test_example_frames(self):
        with open("examples/intro.gdv", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (64, 16, 4))

    def test_bad_magic(self):
        with self.assertRaises(GdvError):
            parse(bytes(16))


if __name__ == "__main__":
    unittest.main()
