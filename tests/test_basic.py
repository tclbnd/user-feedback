import unittest

from neokit.neo import parse

BLANK = bytes(128 + 32000)


class BasicTests(unittest.TestCase):
    def test_resolution(self):
        out = parse(BLANK)
        self.assertEqual((out["resolution"], out["width"], out["filename"]), ("low", 320, None))

    def test_offset(self):
        self.assertEqual(parse(BLANK)["offset"], [0, 0])
