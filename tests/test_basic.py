import unittest

from proreskit.prores import parse

from streams import stream


class BasicTests(unittest.TestCase):
    def test_single_frame(self):
        out = parse(stream(1, width=1280, height=720))
        self.assertEqual((out["width"], out["height"], out["frames"], out["creator"]), (1280, 720, 1, "apl0"))

    def test_slices(self):
        self.assertEqual(parse(stream(1, slices=12))["slices"], 12)
