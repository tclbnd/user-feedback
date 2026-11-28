import unittest

from tgvkit.tgv import TgvError, parse


class BasicTests(unittest.TestCase):
    def test_example_frames(self):
        with open("examples/intro.tgv", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual(len(out["frames"]), 3)
        self.assertEqual((out["frames"][0]["width"], out["frames"][0]["height"]), (64, 32))

    def test_bad_magic(self):
        with self.assertRaises(TgvError):
            parse(bytes(16))


if __name__ == "__main__":
    unittest.main()
