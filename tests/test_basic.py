import unittest

from cyuvkit.cyuv import CyuvError, parse


class BasicTests(unittest.TestCase):
    def test_example_header(self):
        with open("examples/clip.cyuv", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], out["codec"], len(out["frames"])), (32, 8, "CYUV", 3))

    def test_bad_magic(self):
        with self.assertRaises(CyuvError):
            parse(bytes(32))


if __name__ == "__main__":
    unittest.main()
