import unittest

from imxkit.imx import ImxError, parse


class BasicTests(unittest.TestCase):
    def test_example_frames(self):
        with open("examples/film.imx", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (320, 160, 3))

    def test_bad_magic(self):
        with self.assertRaises(ImxError):
            parse(bytes(16))


if __name__ == "__main__":
    unittest.main()
