import unittest

from mwsckit.mwsc import MwscError, parse


class BasicTests(unittest.TestCase):
    def test_example_header(self):
        with open("examples/lesson.mwsc", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (64, 40, 3))

    def test_bad_magic(self):
        with self.assertRaises(MwscError):
            parse(bytes(16))


if __name__ == "__main__":
    unittest.main()
