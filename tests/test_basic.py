import struct
import unittest

from audkit.aud import parse

EMPTY = struct.pack("<HIIBB", 22050, 0, 0, 0, 1)


class BasicTests(unittest.TestCase):
    def test_rate(self):
        out = parse(EMPTY)
        self.assertEqual((out["sample_rate"], out["chunks"]), (22050, 0))

    def test_codec(self):
        self.assertEqual(parse(EMPTY)["codec"], "westwood_adpcm")
