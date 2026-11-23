import unittest

from edlkit.edl import parse

from lists import cut, edl


class BasicTests(unittest.TestCase):
    def test_title(self):
        self.assertEqual(parse(edl([]))["title"], "TEST")

    def test_cut(self):
        out = parse(edl([cut(1, "R1", "V", "00:00:00:00", "00:00:01:00", "01:00:00:00", "01:00:01:00")]))
        self.assertEqual((out["events"][0]["duration"], out["record_frames"]), (30, 30))
