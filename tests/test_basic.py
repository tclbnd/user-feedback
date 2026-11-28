import unittest

from pictkit.pict import PictError, parse


class BasicTests(unittest.TestCase):
    def test_example_size(self):
        with open("examples/icons.pict", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], out["kind"]), (32, 8, "indexed"))

    def test_not_a_picture(self):
        with self.assertRaises(PictError):
            parse(bytes(64))


if __name__ == "__main__":
    unittest.main()
