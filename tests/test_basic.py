import unittest

from discidkit.discid import parse

TOC = "TRACK 01 AUDIO 0\nTRACK 02 AUDIO 7500\nLEADOUT 15000\n"


class BasicTests(unittest.TestCase):
    def test_counts(self):
        out = parse(TOC)
        self.assertEqual((out["tracks"], out["audio_tracks"], out["data_tracks"]), (2, 2, 0))

    def test_length(self):
        self.assertEqual(parse(TOC)["audio_seconds"], "200")
