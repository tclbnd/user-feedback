import unittest

from supkit.sup import parse

from streams import subtitle


class BasicTests(unittest.TestCase):
    def test_video_size(self):
        out = parse(subtitle(0))
        self.assertEqual((out["width"], out["height"], out["display_sets"]), (1920, 1080, 1))

    def test_object(self):
        obj = parse(subtitle(0, w=100, h=2))["subtitles"][0]["objects"][0]
        self.assertEqual((obj["width"], obj["height"], obj["opaque_pixels"]), (100, 2, 100))
