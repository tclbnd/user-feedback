import unittest

from apngkit.apng import parse

from anims import apng


class BasicTests(unittest.TestCase):
    def test_canvas(self):
        out = parse(apng(3, w=128, h=64))
        self.assertEqual((out["width"], out["height"], out["animated"], out["declared_frames"]), (128, 64, True, 3))

    def test_delays(self):
        self.assertEqual(parse(apng(2, delays=[(1, 20), (1, 20)]))["delays"], ["1/20", "1/20"])
