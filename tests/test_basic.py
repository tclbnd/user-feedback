import unittest

from bmvkit.bmv import BmvError, parse


class BasicTests(unittest.TestCase):
    def test_example_frames(self):
        with open("examples/scene.bmv", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (640, 429, 3))

    def test_bad_magic(self):
        with self.assertRaises(BmvError):
            parse(bytes(16))


if __name__ == "__main__":
    unittest.main()
