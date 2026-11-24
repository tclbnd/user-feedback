import unittest

from jpegkit.jpeg import parse

from files import minimal


class BasicTests(unittest.TestCase):
    def test_size(self):
        out = parse(minimal())
        self.assertEqual((out["process"], out["width"], out["height"], out["subsampling"]), ("baseline", 64, 48, "gray"))

    def test_eoi(self):
        self.assertTrue(parse(minimal())["eoi"])
