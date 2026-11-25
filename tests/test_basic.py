import unittest

from exrkit.exr import parse

from headers import minimal


class BasicTests(unittest.TestCase):
    def test_version(self):
        out = parse(minimal())
        self.assertEqual((out["version"], out["multipart"], len(out["parts"])), (2, False, 1))

    def test_compression(self):
        part = parse(minimal())["parts"][0]
        self.assertEqual((part["compression"], part["line_order"]), ("rle", "increasing_y"))
