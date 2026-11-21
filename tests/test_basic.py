import unittest

from mp4kit.tags import parse

from files import mp4, mvhd, meta, text, udta


class BasicTests(unittest.TestCase):
    def test_duration(self):
        out = parse(mp4(mvhd(90_500, 1000)))
        self.assertEqual((out["timescale"], out["duration"]), (1000, "181/2"))

    def test_title(self):
        out = parse(mp4(mvhd(1000), udta(meta(text("©nam", "Morning"), text("©ART", "Choir")))))
        self.assertEqual(out["tags"], {"©nam": "Morning", "©ART": "Choir"})
