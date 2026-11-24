import unittest

from tgakit.tga import parse

from images import tiny


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(tiny())
        self.assertEqual((out["type"], out["width"], out["height"], out["depth"]), ("truecolor", 2, 1, 24))

    def test_pixels(self):
        out = parse(tiny())
        self.assertEqual((out["pixels"], out["complete"]), (2, True))
