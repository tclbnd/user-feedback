import unittest

from cplkit.cpl import parse

from playlists import cpl


class BasicTests(unittest.TestCase):
    def test_title(self):
        out = parse(cpl([24]))
        self.assertEqual((out["title"], out["edit_rate"], out["segments"]), ("TEST", "24", 1))

    def test_duration(self):
        self.assertEqual(parse(cpl([240, 480]))["duration"], "30")
