import unittest

from cuesplit import naming
from cuesplit.sheet import parse
from cuesplit.tracks import layout


class BasicTests(unittest.TestCase):
    def test_two_tracks(self):
        album = parse('FILE "a.wav" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n  TRACK 02 AUDIO\n    INDEX 01 03:00:00\n')
        t = layout(album, {"a.wav": 44100 * 300})
        self.assertEqual([(x["start"], x["end"]) for x in t], [(0, 44100 * 180), (44100 * 180, 44100 * 300)])

    def test_names(self):
        self.assertEqual(naming.unique(["a.flac", "a.flac"]), ["a.flac", "a (1).flac"])
        self.assertEqual(naming.track_name({"number": 3, "title": "AC/DC?"}), "03 - AC_DC_.flac")


if __name__ == "__main__":
    unittest.main()
