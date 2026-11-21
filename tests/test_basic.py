import unittest

from mp3kit.mp3 import scan

from frames import stream


class BasicTests(unittest.TestCase):
    def test_cbr_frames(self):
        out = scan(stream(10))
        self.assertEqual((out["frames"], out["sample_rate"], out["bitrate_mode"]), (10, 44100, "CBR"))

    def test_duration(self):
        out = scan(stream(25, rate=48000))
        self.assertEqual(out["duration"], "3/5")
