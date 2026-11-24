import unittest

from thpkit.thp import parse

from movies import movie


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(movie())
        self.assertEqual((out["version"], out["frames"], out["video"]), ("1.0", 3, {"width": 320, "height": 240}))

    def test_walk(self):
        out = parse(movie(frames=4))
        self.assertEqual((out["frames_walked"], out["video_bytes"]), (4, 3968))
