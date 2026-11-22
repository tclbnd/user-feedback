import unittest

from rmkit.rm import parse

from files import clip, ra, rv


class BasicTests(unittest.TestCase):
    def test_streams(self):
        out = parse(clip(60000, ra(4, 22050, 16, 1, "28_8"), rv("RV30", 320, 240, 15), n=5))
        self.assertEqual([(s["type"], s["codec"]) for s in out["streams"]], [("audio", "28_8"), ("video", "RV30")])

    def test_packets(self):
        out = parse(clip(60000, ra(4), None, n=7))
        self.assertEqual((out["streams"][0]["packets"], out["data_chunks"], out["preroll"]), (7, 1, 3000))
