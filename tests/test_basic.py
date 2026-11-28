import unittest

from toonkit.toons import ToonError, parse


class BasicTests(unittest.TestCase):
    def test_example_frames(self):
        with open("examples/cartoon.toon", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (64, 32, 4))

    def test_bad_magic(self):
        with self.assertRaises(ToonError):
            parse(bytes(16))


if __name__ == "__main__":
    unittest.main()
